<#
.SYNOPSIS
    Одноразовая настройка защищённого хранилища секретов для PERCo Backup.

.DESCRIPTION
    Запускать ОДИН РАЗ вручную, от имени администратора (обычный интерактивный вход,
    НЕ обязательно от SYSTEM). Скрипт запросит пароль SMTP и токен Telegram-бота,
    зашифрует их с помощью DPAPI в режиме LocalMachine (шифрование привязано
    к конкретному компьютеру, а не к пользователю) и сохранит в защищённый файл.
    Благодаря LocalMachine-scope расшифровать секреты сможет любой процесс на этом
    же компьютере, включая задачу Task Scheduler, запущенную от SYSTEM.

    Секреты НИКОГДА не хранятся в открытом виде на диске.
#>

#Requires -RunAsAdministrator

$secretsDir  = "C:\ProgramData\PercoBackup"
$secretsFile = Join-Path $secretsDir "secrets.json"

Add-Type -AssemblyName System.Security

function Protect-Secret {
    param([string]$PlainText)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($PlainText)
    $encrypted = [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    return [Convert]::ToBase64String($encrypted)
}

# --- Создаём папку с ограниченным доступом ---
if (!(Test-Path $secretsDir)) {
    $null = New-Item -ItemType Directory -Force -Path $secretsDir
}

# Оставляем доступ только SYSTEM и локальным Администраторам
icacls $secretsDir /inheritance:r | Out-Null
icacls $secretsDir /grant "SYSTEM:(OI)(CI)F" | Out-Null
icacls $secretsDir /grant "BUILTIN\Administrators:(OI)(CI)F" | Out-Null

Write-Host "=== Настройка секретов для PERCo Backup ===" -ForegroundColor Cyan

$smtpPasswordSecure = Read-Host -AsSecureString -Prompt "Введите SMTP-пароль (Auto_EPM-ITUA_OneDriveBackup@epam.com)"
$tgTokenSecure       = Read-Host -AsSecureString -Prompt "Введите Telegram Bot Token"

$smtpPasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($smtpPasswordSecure)
)
$tgTokenPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($tgTokenSecure)
)

$secrets = @{
    SmtpPassword   = Protect-Secret -PlainText $smtpPasswordPlain
    TelegramToken  = Protect-Secret -PlainText $tgTokenPlain
}

# Затираем открытые копии в памяти как можно скорее
$smtpPasswordPlain = $null
$tgTokenPlain = $null
[GC]::Collect()

$secrets | ConvertTo-Json | Set-Content -Path $secretsFile -Encoding UTF8

# Файл секретов - тоже только SYSTEM и Администраторы
icacls $secretsFile /inheritance:r | Out-Null
icacls $secretsFile /grant "SYSTEM:F" | Out-Null
icacls $secretsFile /grant "BUILTIN\Administrators:F" | Out-Null

Write-Host "Готово. Секреты сохранены (зашифрованы) в $secretsFile" -ForegroundColor Green
Write-Host "Теперь можно запускать основной backup-скрипт из Task Scheduler под SYSTEM." -ForegroundColor Green
