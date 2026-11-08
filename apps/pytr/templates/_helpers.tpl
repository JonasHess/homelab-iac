{{- /* The pip requirement for pytr with its playwright extra: see pytr.pytrCommit. */ -}}
{{- define "pytr.requirement" -}}
{{- if .Values.pytr.pytrCommit -}}
pytr[playwright] @ https://github.com/pytr-org/pytr/archive/{{ .Values.pytr.pytrCommit }}.tar.gz
{{- else -}}
pytr[playwright]=={{ .Values.pytr.pytrVersion }}
{{- end -}}
{{- end -}}
