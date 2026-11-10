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

{{- define "sftpwarden.identityPreflight" -}}
{{- $config := fromYaml .Values.sftpwardenConfig -}}
{{- $provider := get $config "provider" | default dict -}}
{{- if ne (get $provider "type") .Values.provider.type -}}
{{- fail "provider.type must match the provider in sftpwardenConfig." -}}
{{- end -}}
{{- if has .Values.provider.type (list "yaml" "csv" "sqlite") -}}
{{- if or (not .Values.persistence.provider.enabled) (ne (get $provider "path") .Values.provider.path) (ne (dir .Values.provider.path) "/etc/sftpwarden/provider-data") -}}
{{- fail "File providers require enabled provider persistence and matching paths in /etc/sftpwarden/provider-data." -}}
{{- end -}}
{{- end -}}
{{- $name := include "sftpwarden.name" . -}}
{{- $namespace := include "sftpwarden.namespace" . -}}
{{- $workload := lookup "apps/v1" "StatefulSet" $namespace $name -}}
{{- if $workload -}}
{{- $strategy := $workload.spec.updateStrategy | default dict -}}
{{- if ne (get $strategy "type") "OnDelete" -}}
{{- fail "Server identity has not been prepared for explicit activation. Run sftpwarden kube prepare before upgrading; do not delete the current Pod." -}}
{{- end -}}
{{- $durable := false -}}
{{- range $workload.spec.template.spec.volumes -}}
{{- if and (eq .name "host-keys") (hasKey . "persistentVolumeClaim") -}}
{{- $durable = and (not $.Values.hostKeys.secretName) (eq .persistentVolumeClaim.claimName (printf "%s-host-keys" $name)) -}}
{{- end -}}
{{- end -}}
{{- if not $durable -}}
{{- $receipt := lookup "v1" "ConfigMap" $namespace (printf "%s-identity-prepared" $name) | default dict -}}
{{- $data := get $receipt "data" | default dict -}}
{{- $source := printf "pvc:%s-host-keys" $name -}}
{{- if $.Values.hostKeys.secretName -}}{{- $source = printf "secret:%s" $.Values.hostKeys.secretName -}}{{- end -}}
{{- if or (ne (get $data "workloadUID") $workload.metadata.uid) (ne (get $data "source") $source) -}}
{{- fail "Server identity has not been prepared for this workload and source. Run sftpwarden kube prepare before upgrading; do not delete the current Pod." -}}
{{- end -}}
{{- if $.Values.hostKeys.secretName -}}
{{- $secret := lookup "v1" "Secret" $namespace $.Values.hostKeys.secretName | default dict -}}
{{- $metadata := get $secret "metadata" | default dict -}}
{{- if ne (get $metadata "resourceVersion") (get $data "secretVersion") -}}
{{- fail "Host-key Secret changed after identity preparation. Repeat the preparation before upgrading." -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
