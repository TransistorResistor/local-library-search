param([string]$Config = (Join-Path $PSScriptRoot 'local-search.json'))

$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create the project .venv and install requirements-local-search.txt first. See README.md.'
}
$configPath = [System.IO.Path]::GetFullPath($Config)
$launchArguments = @('-m', 'local_search', '--config', ('"' + $configPath + '"'), 'gui')
Start-Process -FilePath $pythonPath -ArgumentList $launchArguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
