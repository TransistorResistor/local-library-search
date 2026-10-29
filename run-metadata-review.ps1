param([string]$Config = (Join-Path $PSScriptRoot 'local-search.json'))
$metadataPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $metadataPython)) { $metadataPython = 'pythonw.exe' }
$metadataArguments = @('-m', 'local_search.metadata_gui', '--config', ('"' + [System.IO.Path]::GetFullPath($Config) + '"'))
Start-Process -FilePath $metadataPython -ArgumentList $metadataArguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
