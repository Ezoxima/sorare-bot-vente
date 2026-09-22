"""Transport GraphQL vers l'API Sorare.

Repris du client éprouvé de Pickdeck, réduit à ce dont la remise en vente a
besoin, avec **une** addition qui lui est propre.

Cette addition : l'API Sorare a **deux** façons de signaler un échec, et une
seule ressemble à une erreur.

1. `errors` au niveau de la réponse — requête invalide, droit manquant. Le
   transport lève.
2. `errors` **à l'intérieur du payload** d'une mutation
   (`createSingleSaleOffer { errors { message } }`). Là, le HTTP vaut 200, le
   bloc `data` est bien rempli, et l'opération a pourtant échoué.

Le cas 2 est celui qui compte ici : une mutation de marché qui échoue en silence
laisserait croire qu'une carte est remise en vente alors qu'elle ne l'est pas.
`executer_mutation` refuse donc de rendre un payload dont `errors` n'est pas
vide. Mesuré le 2026-09-19 : `prepareOffer` rend bien `errors: []` en cas de
succès, et une carte inexistante remonte en `errors` de niveau requête.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from vitrine.config import Config

logger = logging.getLogger(__name__)

URL_GRAPHQL = "https://api.sorare.com/graphql"

# Statuts transitoires : un nouvel essai a du sens.
STATUTS_REESSAYABLES = frozenset({429, 500, 502, 503, 504})


class ErreurSorare(RuntimeError):
    """Échec transport, requête GraphQL invalide, ou payload en erreur."""


class ClientSorare:
    """Client GraphQL. `transport`, `sleep` et `monotonic` sont injectables pour
    tester sans réseau ni attente réelle."""

    def __init__(
        self,
        config: Config,
        *,
        url: str = URL_GRAPHQL,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        max_essais: int = 3,
        backoff_base: float = 0.5,
        intervalle_min: float = 0.35,  # ~170 appels/min, sous le plafond de 200
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._url = url
        self._max_essais = max_essais
        self._backoff_base = backoff_base
        self._intervalle_min = intervalle_min
        self._sleep = sleep
        self._monotonic = monotonic
        self._dernier_appel: float | None = None
        self._http = httpx.Client(
            timeout=timeout, headers={"User-Agent": "vitrine/0.1"}, transport=transport
        )
        self.dernieres_entetes: httpx.Headers | None = None

    def _entetes(self) -> dict[str, str]:
        entetes = {"Content-Type": "application/json"}
        if self._config.cle_api:
            entetes["APIKEY"] = self._config.cle_api
        # Les deux vont ensemble : `Authorization` sans `JWT-AUD` est rejeté.
        if self._config.jwt and self._config.jwt_aud:
            entetes["Authorization"] = f"Bearer {self._config.jwt}"
            entetes["JWT-AUD"] = self._config.jwt_aud
        return entetes

    def _espacer(self) -> None:
        if self._intervalle_min > 0 and self._dernier_appel is not None:
            attente = self._intervalle_min - (self._monotonic() - self._dernier_appel)
            if attente > 0:
                self._sleep(attente)
        self._dernier_appel = self._monotonic()

    def _delai_avant_reessai(self, reponse: httpx.Response, essai: int) -> float:
        """`Retry-After` fait foi quand il est là : c'est l'API qui sait."""
        entete = reponse.headers.get("Retry-After")
        if entete:
            try:
                return float(entete)
            except ValueError:
                pass  # format date HTTP non géré → backoff
        return self._backoff_base * (2**essai)

    def executer(self, requete: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Exécute une requête et rend le bloc `data`. Lève sur `errors` de niveau requête."""
        corps = {"query": requete, "variables": variables or {}}
        derniere: ErreurSorare | None = None

        for essai in range(self._max_essais + 1):
            self._espacer()
            try:
                reponse = self._http.post(self._url, json=corps, headers=self._entetes())
            except httpx.HTTPError as exc:
                derniere = ErreurSorare(f"Erreur transport vers l'API Sorare : {exc}")
                if essai < self._max_essais:
                    self._sleep(self._backoff_base * (2**essai))
                    continue
                raise derniere from exc

            self.dernieres_entetes = reponse.headers
            logger.debug(
                "sorare complexite=%s profondeur=%s statut=%s",
                reponse.headers.get("x-gql-complexity"),
                reponse.headers.get("x-gql-depth"),
                reponse.status_code,
            )

            if reponse.status_code in STATUTS_REESSAYABLES:
                derniere = ErreurSorare(f"HTTP {reponse.status_code} (transitoire).")
                if essai < self._max_essais:
                    self._sleep(self._delai_avant_reessai(reponse, essai))
                    continue
                raise derniere

            if reponse.status_code >= 400:
                raise ErreurSorare(f"HTTP {reponse.status_code} : {reponse.text[:500]}")

            charge = reponse.json()
            if charge.get("errors"):
                raise ErreurSorare(f"Erreurs GraphQL : {charge['errors']}")
            return charge.get("data") or {}

        raise derniere or ErreurSorare("Échec de la requête Sorare.")

    def executer_mutation(
        self, requete: str, variables: dict[str, Any], racine: str
    ) -> dict[str, Any]:
        """Comme `executer`, mais refuse un payload dont `errors` n'est pas vide.

        `racine` est le nom du champ de mutation (`prepareOffer`,
        `createSingleSaleOffer`, `cancelOffer`). Voir la docstring du module :
        c'est l'échec qui ne ressemble pas à un échec.
        """
        payload = self.executer(requete, variables).get(racine)
        if payload is None:
            raise ErreurSorare(f"Mutation {racine} : payload absent de la réponse.")
        erreurs = payload.get("errors") or []
        if erreurs:
            details = "; ".join(e.get("message", "?") for e in erreurs)
            raise ErreurSorare(f"Mutation {racine} refusée par Sorare : {details}")
        return payload

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ClientSorare:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
