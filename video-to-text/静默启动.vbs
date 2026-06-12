Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptPath = "D:\ProgramData\TraeCN\NoteForge\video-to-text\web_server.py"
pythonPath = "D:\ProgramData\TraeCN\NoteForge\video-to-text\envs\paraformer\python.exe"
workDir = "D:\ProgramData\TraeCN\NoteForge\video-to-text"

If Not fso.FileExists(pythonPath) Then
    MsgBox "错误: 找不到 Python 环境" & vbCrLf & pythonPath, 16, "NoteForge"
    WScript.Quit
End If

If Not fso.FileExists(scriptPath) Then
    MsgBox "错误: 找不到 Web 服务脚本" & vbCrLf & scriptPath, 16, "NoteForge"
    WScript.Quit
End If

WshShell.CurrentDirectory = workDir

cmd = """" & pythonPath & """ """ & scriptPath & """"
WshShell.Run "cmd /c " & cmd, 0, False

WScript.Sleep 4000

WshShell.Run "http://localhost:5000"

WScript.Sleep 500

msg = "✅ NoteForge 服务已启动!" & vbCrLf & vbCrLf
msg = msg & "📱 浏览器已打开，可以开始使用转写服务" & vbCrLf & vbCrLf
msg = msg & "💡 提示:" & vbCrLf
msg = msg & "   - 关闭此窗口不影响服务运行" & vbCrLf
msg = msg & "   - 需停止服务时运行 停止服务.bat"

MsgBox msg, 64, "NoteForge"