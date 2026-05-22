
Attribute VB_Name = "ASGL_RunnerUI"
Option Explicit

' Modeless progress UI implemented via a simple worksheet panel (no form dependencies).
' If you prefer a VBA UserForm, this module is the central abstraction point.

Private Function EnsureRunnerSheet() As Worksheet
    Dim ws As Worksheet
    On Error Resume Next
    Set ws = ThisWorkbook.Worksheets("_ASGL_RUNNER")
    On Error GoTo 0
    If ws Is Nothing Then
        Set ws = ThisWorkbook.Worksheets.Add(After:=ThisWorkbook.Worksheets(ThisWorkbook.Worksheets.Count))
        ws.Name = "_ASGL_RUNNER"
        ws.Visible = xlSheetVeryHidden
        ws.Range("A1").Value = "ASGL Runner"
        ws.Range("A3").Value = "Status:"
        ws.Range("A4").Value = "Progress:"
        ws.Range("A5").Value = "Job ID:"
        ws.Range("B3:B5").Value = ""
    End If
    Set EnsureRunnerSheet = ws
End Function

Public Sub RunnerShow()
    Dim ws As Worksheet: Set ws = EnsureRunnerSheet()
    ws.Visible = xlSheetVisible
    ws.Activate
    ws.Range("A1").Select
End Sub

Public Sub RunnerHide()
    On Error Resume Next
    ThisWorkbook.Worksheets("_ASGL_RUNNER").Visible = xlSheetVeryHidden
    On Error GoTo 0
End Sub

Public Sub RunnerUpdate(ByVal status As String, ByVal progress As Double, Optional ByVal jobId As String = "")
    On Error Resume Next
    Dim ws As Worksheet: Set ws = EnsureRunnerSheet()
    ws.Range("B3").Value = status
    ws.Range("B4").Value = CInt(progress * 100) & "%"
    If Len(jobId) > 0 Then ws.Range("B5").Value = jobId
    On Error GoTo 0
End Sub
