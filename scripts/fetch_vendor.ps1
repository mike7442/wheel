# clone the pinned third-party firmware (vendor/ is gitignored)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dst = Join-Path $root "vendor\ALIFE-MM32"
$url = "https://gitlab.com/ailife8881/Hoverboard-Firmware-Hack-Gen2.x-MM32.git"
$commit = "68f880d47947a9cfaf3fc4fe6ca1fca5e824e271"

if (Test-Path (Join-Path $dst ".git")) {
    Write-Host "already cloned: $dst"
} else {
    git clone $url $dst
}
git -C $dst checkout $commit
Write-Host "vendor at $commit"
