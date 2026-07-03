Set WshShell = CreateObject("WScript.Shell")
' app.pyがあるディレクトリのパスを特定して移動
Dim currentPath
currentPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptPosition)
WshShell.CurrentDirectory = currentPath

' 0 はウィンドウを非表示にする設定
WshShell.Run """C:\Users\ookub\AppData\Local\Programs\Python\Python312\python.exe"" app.py", 0, False
