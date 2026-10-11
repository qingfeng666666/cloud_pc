' 云电脑 — 主机静默启动（先杀旧进程再启动）
Set ws = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
ws.CurrentDirectory = fs.GetParentFolderName(WScript.ScriptFullName)
ws.Environment("PROCESS")("PYTHONPATH") = ws.CurrentDirectory & "\..\shared_components"

' 杀掉旧的主机进程
ws.Run "taskkill /f /im pythonw.exe", 0, True

' 启动新主机
ws.Run "pythonw -m host.main", 0, False
