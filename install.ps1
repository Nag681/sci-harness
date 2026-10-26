# Online installer for Sci-Harness, the sci agent skill, on Windows (PowerShell 5.1 or 7).
#
#   PowerShell:   irm https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 | iex
#   cmd.exe:      curl -fsSLo %TEMP%\sci.ps1 https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 && powershell -NoProfile -ExecutionPolicy Bypass -File %TEMP%\sci.ps1
#
# Options go after the -File path in cmd.exe. In PowerShell, set them first:
#   $env:SCI_ARGS = "--hooks"; irm https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 | iex
#
# Downloads the repository and runs its install.py with the arguments given (--help lists them).
# Needs Python 3.8+. Optional environment variables:
#   SCI_REF     branch, tag or commit to install (default: main)
#   SCI_REPO    GitHub owner/name (default: Nag681/sci-harness)
#   SCI_SOURCE  install from this local folder or .zip instead of downloading
#   SCI_ARGS    install.py arguments when the script is piped to iex
#   GH_TOKEN    or GITHUB_TOKEN, to install from a private fork
#
# Everything runs inside one script block, so nothing is left behind in the calling session.

& {
    param([string[]]$InstallArgs)
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'  # Windows PowerShell 5.1 downloads very slowly with a progress bar

    function Find-Python {
        # Each candidate is run once: python and python3 can be Microsoft Store stubs that only print a hint.
        # 'Continue': Windows PowerShell 5.1 turns a native program's stderr into errors, fatal under 'Stop'.
        $ErrorActionPreference = 'Continue'
        foreach ($cand in @(@('py', '-3'), @('python'), @('python3'))) {
            if (-not (Get-Command $cand[0] -CommandType Application -ErrorAction SilentlyContinue)) { continue }
            $rest = @($cand | Select-Object -Skip 1)
            & $cand[0] @rest -c 'import sys; sys.exit(sys.version_info < (3, 8))' *> $null
            if ($LASTEXITCODE -eq 0) { return , $cand }
        }
        return $null
    }

    if ($PSVersionTable.PSVersion.Major -lt 5) { throw 'sci-harness: PowerShell 5.1 or newer is required.' }
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor
            [Net.SecurityProtocolType]::Tls12
    } catch { }

    $InstallArgs = @($InstallArgs | Where-Object { $_ })
    if ($InstallArgs.Count -eq 0 -and $env:SCI_ARGS) {
        $InstallArgs = @($env:SCI_ARGS -split '\s+' | Where-Object { $_ })
    }
    $repo = if ($env:SCI_REPO) { $env:SCI_REPO } else { 'Nag681/sci-harness' }
    $ref = if ($env:SCI_REF) { $env:SCI_REF } else { 'main' }
    $token = if ($env:GH_TOKEN) { $env:GH_TOKEN } else { $env:GITHUB_TOKEN }

    $python = Find-Python
    if (-not $python) {
        throw ('sci-harness: Python 3.8+ is required. Install it (winget install Python.Python.3.12, or ' +
               'https://www.python.org/downloads/) and run this again.')
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Warning 'sci-harness: git not found; the skill needs git in every repository it manages.'
    }

    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('sci-harness-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $src = $env:SCI_SOURCE
        if ($src -and (Test-Path -LiteralPath $src -PathType Container)) {
            $dir = $src
        } else {
            $zip = Join-Path $tmp 'source.zip'
            if ($src) {
                Copy-Item -LiteralPath $src -Destination $zip
            } else {
                $headers = @{}
                if ($token) {
                    $url = "https://api.github.com/repos/$repo/zipball/$ref"
                    $headers['Authorization'] = "Bearer $token"
                } else {
                    $url = "https://github.com/$repo/archive/$ref.zip"
                }
                Write-Host "sci-harness: downloading $repo ($ref)"
                Invoke-WebRequest -Uri $url -OutFile $zip -Headers $headers -UseBasicParsing
            }
            Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
            $found = Get-ChildItem -LiteralPath $tmp -Directory |
                Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'install.py') } |
                Select-Object -First 1
            if (-not $found) { throw 'sci-harness: install.py not found in the downloaded archive.' }
            $dir = $found.FullName
        }
        # Not captured, so install.py writes straight to the console. 'Continue' for the reason above.
        $ErrorActionPreference = 'Continue'
        $rest = @($python | Select-Object -Skip 1)
        & $python[0] @rest (Join-Path $dir 'install.py') @InstallArgs
        $code = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($code -ne 0) { throw "sci-harness: install.py failed (exit code $code)." }
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
} -InstallArgs $args
