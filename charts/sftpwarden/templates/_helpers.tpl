{{- define "sftpwarden.name" -}}
{{- default .Chart.Name .Values.kubernetes.release | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sftpwarden.namespace" -}}
{{- default .Release.Namespace .Values.kubernetes.namespace -}}
{{- end -}}

{{- define "sftpwarden.labels" -}}
app.kubernetes.io/name: sftpwarden
app.kubernetes.io/instance: {{ include "sftpwarden.name" . }}
app.kubernetes.io/component: runtime
app.kubernetes.io/part-of: sftpwarden
app.kubernetes.io/managed-by: Helm
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "sftpwarden.selectorLabels" -}}
app.kubernetes.io/name: sftpwarden
app.kubernetes.io/instance: {{ include "sftpwarden.name" . }}
app.kubernetes.io/component: runtime
{{- end -}}

{{- define "sftpwarden.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) -}}
{{- end -}}
