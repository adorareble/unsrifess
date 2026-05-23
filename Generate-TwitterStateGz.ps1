$content = Get-Content -Raw twitter_state.json
$bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
$ms = New-Object System.IO.MemoryStream
$gzip = New-Object System.IO.Compression.GzipStream($ms, [System.IO.Compression.CompressionMode]::Compress, $true)
$gzip.Write($bytes, 0, $bytes.Count); $gzip.Close()
[Convert]::ToBase64String($ms.ToArray()) | Set-Clipboard
Write-Output "Copied! Length: $($ms.Length) chars"
