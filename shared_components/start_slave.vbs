' 云电脑 — 从机静默启动（修改下面两行）
SHARE_ROOT = "I:\"
SLAVE_NAME = "MyServer"

' ============================================
Set ws = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
ws.CurrentDirectory = fs.GetParentFolderName(WScript.ScriptFullName)

cmd = "pythonw -m slave.main --root """ & SHARE_ROOT & """ --name """ & SLAVE_NAME & """"
ws.Run cmd, 0, False
