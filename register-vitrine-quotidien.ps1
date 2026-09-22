# register-vitrine-quotidien.ps1 - installe la tache planifiee Windows du
# passage quotidien (reprise du patron eprouve de Pickdeck, voir
# sorare_app_v2/register-odds-football-morning-task.ps1).
#
# Declenchement quotidien a 08h00, avant le run 09h00 de Pickdeck (cf. plan :
# ne pas se disputer le quota API Sorare). -WakeToRun + -StartWhenAvailable :
# reveille une veille, rattrape au demarrage si l'heure a ete manquee.
# -MultipleInstances IgnoreNew : si un passage precedent tourne encore (reseau
# lent, API qui retente), le declencheur suivant est ignore plutot que de
# lancer une deuxieme instance en parallele -- deux passages simultanes
# pourraient publier deux fois la meme carte.
#
# /!\ Ce script REGISTRE une vraie tache planifiee Windows -- a lancer
# uniquement par l'utilisateur, apres relecture, jamais automatiquement par
# une session Claude. Ne l'enregistrer qu'apres l'etape 6 du plan (plusieurs
# jours de passages a blanc relus) -- pas avant.
#
# Reversibilite : Unregister-ScheduledTask -TaskName "VitrineQuotidien" -Confirm:$false

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$scriptPath = Join-Path $root 'vitrine-quotidien.ps1'
if (-not (Test-Path $scriptPath)) {
    throw "vitrine-quotidien.ps1 introuvable a la racine du repo : $scriptPath"
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -Daily -At "08:00"

# -AllowStartIfOnBatteries/-DontStopIfGoingOnBatteries : New-ScheduledTaskSettingsSet
# bloque par defaut tout demarrage sur batterie (piege deja paye cote Pickdeck,
# E47.7, 2026-08-20 -- constate en conditions reelles). Sans ce reglage, le
# passage quotidien ne tournerait que quand le PC est branche a 08h00.
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

try {
    Register-ScheduledTask -TaskName "VitrineQuotidien" `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Vitrine : repositionnement quotidien des prix, sous plancher (08h00, avant Pickdeck 09h00)." `
        -ErrorAction Stop | Out-Null
} catch {
    throw "Register-ScheduledTask VitrineQuotidien a echoue : $($_.Exception.Message)"
}

Write-Host "Tache 'VitrineQuotidien' enregistree : quotidienne a 08h00 (reveil + rattrapage au demarrage)." -ForegroundColor Green
Write-Host "Rappel : vitrine-quotidien.ps1 tourne en mode a blanc par defaut (pas de -Executer)." -ForegroundColor Yellow
