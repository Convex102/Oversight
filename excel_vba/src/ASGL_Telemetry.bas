
Attribute VB_Name = "ASGL_Telemetry"
Option Explicit

Public Function GetWorkbookFingerprint() As String
    On Error Resume Next
    Dim s As String
    s = ThisWorkbook.FullName & "|" & ThisWorkbook.Name & "|" & CStr(ThisWorkbook.BuiltinDocumentProperties("Last Save Time"))
    GetWorkbookFingerprint = SimpleHash(s)
End Function

Public Function GetTenantId() As String
    GetTenantId = GetSetting("tenant_id", "")
End Function

Public Sub EnsureTenantId()
    Dim t As String: t = GetTenantId()
    If Len(t) = 0 Then
        t = InputBox("Enter Tenant/Firm ID (for telemetry & template library):", "ASGL Setup")
        If Len(t) > 0 Then SetSetting "tenant_id", t
    End If
End Sub

Public Function BuildTelemetryContext() As String
    Dim ctx As String
    ctx = "tenant_id=" & GetTenantId() & ";user=" & Environ$("USERNAME") & ";wb_fp=" & GetWorkbookFingerprint()
    BuildTelemetryContext = ctx
End Function

Private Function SimpleHash(ByVal s As String) As String
    Dim i As Long, h As Double
    h = 2166136261#
    For i = 1 To Len(s)
        h = (h Xor Asc(Mid$(s, i, 1))) * 16777619#
        If h > 2 ^ 31 Then h = h - 2 ^ 31
    Next i
    SimpleHash = Hex$(CLng(h))
End Function
