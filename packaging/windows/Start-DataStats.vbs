' DataStats Workbench - silent launcher. Keep this file ASCII-only.
Option Explicit
Dim fso, shell, root, pythonw, launcher, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\runtime\python\pythonw.exe"
launcher = root & "\launcher.py"
If Not fso.FileExists(pythonw) Then
  MsgBox "The application runtime is missing." & vbCrLf & "Please extract the complete package again.", vbCritical, "DataStats Workbench"
  WScript.Quit 1
End If
If Not fso.FileExists(launcher) Then
  MsgBox "The launcher is missing. Please extract the complete package again.", vbCritical, "DataStats Workbench"
  WScript.Quit 1
End If
shell.CurrentDirectory = root
command = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & launcher & Chr(34)
shell.Run command, 0, False
