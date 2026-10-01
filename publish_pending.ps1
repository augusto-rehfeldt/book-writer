# Uploads every archived KDP package that is not submitted yet, oldest book first.
# Run by the scheduled task "BookWriter KDP pending"; safe to run by hand at any time.
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$log = Join-Path $PSScriptRoot 'book_output\publish_pending.log'

$pending = Get-ChildItem 'book_output\archive\ebooks' -Recurse -Filter '*_kdp.json' |
    ForEach-Object { [pscustomobject]@{ Path = $_.FullName; Package = Get-Content $_.FullName -Raw | ConvertFrom-Json } } |
    Where-Object { $_.Package.kdp_status -ne 'submitted' } |
    Sort-Object { $_.Package.created_at } |
    ForEach-Object { $_.Path }

"$(Get-Date -Format s) pending: $($pending.Count)" | Add-Content $log
if ($pending) {
    python -B -m ai_book_creator.utils.kdp_publisher @pending 2>&1 |
        Where-Object { "$_" -notmatch 'chromedriver!|KERNEL32|ntdll' } |
        Add-Content $log
    "$(Get-Date -Format s) exit code: $LASTEXITCODE" | Add-Content $log
}
