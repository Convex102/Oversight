Attribute VB_Name = "ASGLRunner"
Option Explicit
Private Const API_BASE As String = "http://localhost:8000"
Public ASGL_API_KEY As String

Public Sub SetApiKey()
    ASGL_API_KEY = InputBox("Enter X-API-Key")
End Sub
