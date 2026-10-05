param([Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
function Await-Operation($operation, $resultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $method.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}
$fileType = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$streamType = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$decoderType = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$bitmapType = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$resultType = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
$engineType = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$language = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]::new('en-US')
$engine = $engineType::TryCreateFromLanguage($language)
if ($null -eq $engine) { throw 'Windows English OCR language is unavailable.' }
$file = Await-Operation ($fileType::GetFileFromPathAsync($ImagePath)) $fileType
$stream = Await-Operation ($file.OpenAsync([Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]::Read)) $streamType
try {
    $decoder = Await-Operation ($decoderType::CreateAsync($stream)) $decoderType
    $bitmap = Await-Operation ($decoder.GetSoftwareBitmapAsync()) $bitmapType
    try {
        $result = Await-Operation ($engine.RecognizeAsync($bitmap)) $resultType
        $words = @(foreach ($line in $result.Lines) {
            foreach ($word in $line.Words) {
                @{ text=$word.Text; x=$word.BoundingRect.X; y=$word.BoundingRect.Y;
                   width=$word.BoundingRect.Width; height=$word.BoundingRect.Height }
            }
        })
        @{ text=$result.Text; words=$words; engine='Windows.Media.Ocr'; confidence=$null } | ConvertTo-Json -Depth 6 -Compress
    } finally { $bitmap.Dispose() }
} finally { $stream.Dispose() }
