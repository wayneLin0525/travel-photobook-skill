# Windows-native bootstrap. No Python, Node, Git, or administrator account required.
$ErrorActionPreference = 'Stop'
$engineArguments = $args
if ($env:PHOTOBOOK_ENGINE) { & $env:PHOTOBOOK_ENGINE @engineArguments; exit $LASTEXITCODE }
$skillDir = Split-Path -Parent $PSScriptRoot
$arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLower()
if ($arch -eq 'x64') { $platform = 'windows-x64' }
elseif ($arch -eq 'arm64') { $platform = 'windows-arm64' }
else { throw '尚未发布此Windows架构的运行包。' }
$cache = if ($env:PHOTOBOOK_CACHE) { $env:PHOTOBOOK_CACHE } else { Join-Path $env:LOCALAPPDATA 'travel-photobook' }
$manifest = Join-Path $skillDir "assets/runtime/$platform"
if (!(Test-Path "$manifest.url") -or !(Test-Path "$manifest.sha256")) { throw '尚未配置已发布运行包，技能说明下载不等于安装完成。' }
$url = (Get-Content "$manifest.url" -Raw).Trim()
$expected = (Get-Content "$manifest.sha256" -Raw).Trim()
if ($expected -notmatch '^[0-9a-f]{64}$') { throw '运行包校验值无效。' }
$localRelease = $url.StartsWith('file://') -and $env:PHOTOBOOK_ALLOW_LOCAL_RELEASE -eq '1'
if (!$url.StartsWith('https://') -and !$localRelease) { throw '运行包地址必须是HTTPS。' }
$parent = Join-Path $cache 'runtime/0.2.1'
$target = Join-Path $parent "$platform-$expected"
$engine = Join-Path $target 'photobook-engine/photobook-engine.exe'
if (!(Test-Path $engine) -or !(Test-Path (Join-Path $target '.ready'))) {
    New-Item -ItemType Directory -Force $parent | Out-Null
    $staging = Join-Path $parent ('.install-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory $staging | Out-Null
    try {
        $archive = Join-Path $staging 'runtime.zip'
        if ($localRelease) { Copy-Item ([uri]$url).LocalPath $archive }
        else {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $downloaded = $false
            for ($i=0; $i -lt 3; $i++) {
                try { Invoke-WebRequest -UseBasicParsing $url -OutFile $archive; $downloaded=$true; break }
                catch { if ($i -eq 2) { throw } }
            }
            if (!$downloaded) { throw '自动下载运行包失败。' }
        }
        if ((Get-FileHash $archive -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw '运行包SHA-256校验失败。' }
        $unpacked = Join-Path $staging 'unpacked'
        Expand-Archive $archive $unpacked
        if (!(Test-Path (Join-Path $unpacked 'photobook-engine/photobook-engine.exe'))) { throw '运行包缺少入口。' }
        Set-Content -Path (Join-Path $unpacked '.ready') -Value $expected
        if (!(Test-Path $target)) {
            try { [System.IO.Directory]::Move($unpacked,$target) }
            catch { if (!(Test-Path (Join-Path $target '.ready'))) { throw } }
        }
        elseif (!(Test-Path (Join-Path $target '.ready'))) { throw '现有运行包不完整。' }
    } finally { Remove-Item -Recurse -Force $staging }
}
& $engine @engineArguments
exit $LASTEXITCODE
