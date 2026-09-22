# vitrine-quotidien.ps1 - job de la tache planifiee quotidienne (voir
# register-vitrine-quotidien.ps1). Lance `vitrine quotidien`, journalise, et
# ne remonte une erreur que si le programme Python a reellement echoue.
#
# /!\ MODE A BLANC PAR DEFAUT : ce script n'ajoute PAS `--executer`. C'est
# volontaire (cf. plan, etape 6 : plusieurs jours de passages a blanc relus
# avant tout achat/toute vente reelle). Une fois les rapports valides,
# decommenter la ligne `--executer` ci-dessous -- decision de l'utilisateur,
# jamais automatique.
#
# $ErrorActionPreference = 'Continue' (pas 'Stop') : piege deja paye cote
# Pickdeck (sorare_app_v2/sync-odds-football-morning.ps1, E47.7/D49). Sous
# 'Stop', la premiere ligne ecrite par le module `logging` Python sur stderr
# (meme un simple INFO/DEBUG) devient une NativeCommandError TERMINANTE a ce
# niveau, alors meme que le programme reussit et rend le code 0. Le controle
# reel est `$LASTEXITCODE`, verifie explicitement plus bas.
param(
    [switch]$Executer,
    [int]$MaxCartes = 3,
    [double]$BaisseMaxPct = 10.0
)

$ErrorActionPreference = 'Continue'

$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "python du venv introuvable : $python (installer avec 'pip install -e .[dev]')"
}

$logDir = Join-Path $root 'journaux'
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}
$logFile = Join-Path $logDir "quotidien-$(Get-Date -Format 'yyyy-MM-dd').log"

$arguments = @(
    '-m', 'vitrine.cli', 'quotidien',
    '--max-cartes', $MaxCartes,
    '--baisse-max-pct', $BaisseMaxPct
)
if ($Executer) {
    $arguments += '--executer'
}

"$(Get-Date -Format o) -- vitrine quotidien $($arguments -join ' ')" |
    Out-File -Append -Encoding utf8 $logFile
& $python @arguments *>> $logFile

if ($LASTEXITCODE -ne 0) {
    "$(Get-Date -Format o) -- ECHEC (code $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $logFile
    exit 1
}
"$(Get-Date -Format o) -- OK" | Out-File -Append -Encoding utf8 $logFile
