$ErrorActionPreference = "Stop"

# Принудительно включаем TLS 1.2 для работы с Office 365 и Telegram API
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- Settings ---
$sourceFolder      = "C:\PercoDB_Backup\"
$destinationFolder = "D:\BACKUP\"
$retentionDays     = 365

$smtpServer   = "smtp.office365.com"
$smtpPort     = 587
$smtpUsername = "Auto_EPM-ITUA_OneDriveBackup@epam.com"
$senderEmail  = "Auto_EPM-ITUA_OneDriveBackup@epam.com"
$recipientEmails = "Andrei_Trokol@epam.com"
$ccEmails     = "Nikolay_Zinovyev@epam.com", "Dmitriy_Akimov@epam.com", "Kuanysh_Uvaliyev@epam.com", "Rustam_Baratov@epam.com", "Denis_Sribnyy@epam.com"

$tgChatId = "367218525"

# --- Загрузка секретов из защищённого хранилища ---
# Секреты создаются один раз скриптом Setup-PercoBackupSecrets.ps1 (запуск от администратора).
# Шифрование - DPAPI LocalMachine scope, поэтому расшифровать может любой процесс
# на этой машине (включая задачу Task Scheduler под SYSTEM), но не на другом ПК.
$secretsFile = "C:\ProgramData\PercoBackup\secrets.json"

function Unprotect-Secret {
    param([string]$EncryptedBase64)
    Add-Type -AssemblyName System.Security
    $encryptedBytes = [Convert]::FromBase64String($EncryptedBase64)
    $bytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $encryptedBytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    return [System.Text.Encoding]::UTF8.GetString($bytes)
}

if (!(Test-Path $secretsFile)) {
    throw "Secrets file not found at $secretsFile. Run Setup-PercoBackupSecrets.ps1 as Administrator first."
}

$secretsRaw   = Get-Content -Path $secretsFile -Raw | ConvertFrom-Json
$smtpPassword = Unprotect-Secret -EncryptedBase64 $secretsRaw.SmtpPassword
$tgToken      = Unprotect-Secret -EncryptedBase64 $secretsRaw.TelegramToken

# Path variables
$currentDateTime = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$logFileName     = "log-$currentDateTime.txt"
$archiveFileName = "Backup-$currentDateTime.zip"
$logFilePath     = Join-Path $destinationFolder $logFileName
$archiveFilePath = Join-Path $destinationFolder $archiveFileName
$sevenZipCmd     = "C:\Program Files\7-Zip\7z.exe"
$serviceName     = "ss17kService"

$backupSuccess   = $false
$failureReason   = ""
$backupWarning   = $false

# --- Functions ---
function Write-Log {
    param([string]$message)
    $logDateTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $fullMsg = "$logDateTime - $message"
    Write-Host $fullMsg
    if (Test-Path $destinationFolder) {
        $null = Add-Content -Path $logFilePath -Value $fullMsg -ErrorAction SilentlyContinue
    }
}

function Send-Telegram {
    param([string]$message)
    try {
        $url = "https://api.telegram.org/bot$tgToken/sendMessage"
        # Явно кодируем JSON в UTF-8 байты, иначе Windows PowerShell 5.1
        # отправляет не-ASCII символы (эмодзи, кириллицу) в неверной кодировке
        # и Telegram получает "битые" символы (например âœ… вместо ✅).
        $payload = @{ chat_id = $tgChatId; text = $message } | ConvertTo-Json -Compress
        $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
        Invoke-RestMethod -Uri $url -Method Post -Body $bodyBytes -ContentType "application/json; charset=utf-8" | Out-Null
    } catch {
        Write-Log "Telegram Error: $($_.Exception.Message)"
    }
}

# --- Main Job ---
try {
    # Проверка доступности диска D:
    $destDrive = $destinationFolder.Substring(0,2) + "\"
    if (!(Test-Path $destDrive)) {
        throw "Drive $destDrive is NOT available. Check if disk is connected/mounted."
    }

    if (!(Test-Path $destinationFolder)) { $null = New-Item -ItemType Directory -Force -Path $destinationFolder }

    Write-Log "--------------------------------------------------------------------------------"
    Write-Log "PERCo DataBase Backup Start"
    Write-Log "--------------------------------------------------------------------------------"

    # Rotation (Cleanup old) - маска добавлена в путь, чтобы -Include реально работал с -Recurse
    $limit = (Get-Date).AddDays(-$retentionDays)
    $oldFiles = Get-ChildItem -Path "$destinationFolder\*" -Include "*.zip", "log-*.txt" -Recurse |
        Where-Object { $_.LastWriteTime -lt $limit }
    if ($oldFiles) {
        Write-Log "Cleaning up $($oldFiles.Count) files older than $retentionDays days..."
        $oldFiles | Remove-Item -Force
    }

    # Backup Process
    Write-Log "Stopping service '$serviceName'..."
    Stop-Service $serviceName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 15

    Write-Log "Starting archiving process..."
    if (!(Test-Path $sevenZipCmd)) { throw "7-Zip not found at $sevenZipCmd" }

    & $sevenZipCmd a -tzip -mx=3 "$archiveFilePath" "$sourceFolder\*" | Out-String | ForEach-Object {
        $line = $_.Trim()
        if ($line) { Write-Log $line }
    }

    if ($LASTEXITCODE -eq 0) {
        $backupSuccess = $true
        Write-Log "All files successfully added to archive: $archiveFileName"
    } elseif ($LASTEXITCODE -eq 1) {
        $backupSuccess = $true
        $backupWarning = $true
        Write-Log "WARNING: 7-Zip finished with warnings (exit code 1) - some files may have been skipped (e.g. locked DB files)."
    } else {
        throw "7-Zip failed with exit code $LASTEXITCODE"
    }

} catch {
    $backupSuccess = $false
    $failureReason = $_.Exception.Message
    Write-Log "!!! ERROR: $failureReason"
} finally {
    Write-Log "Starting service '$serviceName'..."
    Start-Service $serviceName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5

    # Disk Info
    try {
        $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($destinationFolder.Substring(0,2))'"
        $freeGB = [math]::Round($disk.FreeSpace / 1GB, 2)
        $totalGB = [math]::Round($disk.Size / 1GB, 2)
        $diskInfo = "Disk space: $freeGB GB free of $totalGB GB"
        Write-Log $diskInfo
    } catch { $diskInfo = "Disk info unavailable" }

    # Messaging
    if ($backupSuccess -and -not $backupWarning) {
        $emailBody = "Perco backup was successfully created.`n$diskInfo"
        $tgHeader  = "PERCo Backup: Success"
    } elseif ($backupSuccess -and $backupWarning) {
        $emailBody = "Perco backup completed WITH WARNINGS (some files may have been skipped).`nCheck log: $logFileName`n$diskInfo"
        $tgHeader  = "PERCo Backup: Success with warnings"
    } else {
        $emailBody = "Perco backup FAILED!`nReason: $failureReason`nCheck log: $logFileName`n$diskInfo"
        $tgHeader  = "PERCo Backup: FAILED"
    }

    # Email Sending
    $att = $null
    $mail = $null
    $smtp = $null
    try {
        $smtp = New-Object System.Net.Mail.SmtpClient($smtpServer, $smtpPort)
        $smtp.EnableSsl = $true
        $smtp.Credentials = New-Object System.Net.NetworkCredential($smtpUsername, $smtpPassword)

        $mail = New-Object System.Net.Mail.MailMessage
        $mail.From = $senderEmail
        $mail.Subject = "Perco backup Bishkek"
        $mail.Body = $emailBody

        $recipientEmails.Split(",") | ForEach-Object { if ($_.Trim()) { $mail.To.Add($_.Trim()) } }
        $ccEmails | ForEach-Object { if ($_.Trim()) { $mail.CC.Add($_.Trim()) } }

        if (Test-Path $logFilePath) {
            $att = New-Object System.Net.Mail.Attachment($logFilePath)
            $mail.Attachments.Add($att)
        }

        $smtp.Send($mail)
        Write-Log "Email notification sent successfully."
    } catch {
        Write-Log "Mail Error: $($_.Exception.Message)"
    } finally {
        if ($att) { $att.Dispose() }
        if ($mail) { $mail.Dispose() }
        if ($smtp) { $smtp.Dispose() }
    }

    # Telegram Sending
    Send-Telegram -message "$tgHeader`n$diskInfo"

    Write-Log "--------------------------------------------------------------------------------"
    Write-Log "Script Finished"
}
