{{- /*
  One CronJob, shared by the `cronJobs` map and by purpose-built features such as
  `sqliteBackup`. Call with a dict:

    root  the chart root context ($)
    name  job name; the CronJob is <appName>-<name>
    job   the job settings (see cronJobs in values.schema.json)

  The pod label is app: <appName>-<name>, never the app's own label: the generic
  Service selects on app: <appName>, so a job pod carrying it would receive the
  app's traffic while it runs.

  pvcMounts maps a key of deployment.pvcMounts or persistentVolumeClaims to a path
  in the job container; the job mounts that existing claim, it never creates one.
*/ -}}
{{- define "generic.cronjob" -}}
{{- $root := .root }}
{{- $job := .job }}
{{- $appName := required "appName must be set in values.yaml" $root.Values.appName }}
{{- $jobName := printf "%s-%s" $appName .name }}
{{- $claims := merge (dict) (($root.Values.deployment | default dict).pvcMounts | default dict) ($root.Values.persistentVolumeClaims | default dict) }}
{{- range $claim, $_ := $job.pvcMounts }}
  {{- if not (hasKey $claims $claim) }}
    {{- fail (printf "%s: pvcMounts key %q is not a key of deployment.pvcMounts or persistentVolumeClaims" $jobName $claim) }}
  {{- end }}
{{- end }}
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ $jobName }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    app: {{ $jobName }}
spec:
  schedule: {{ required (printf "%s: schedule must be set" $jobName) $job.schedule | quote }}
  {{- with $job.timeZone }}
  timeZone: {{ . | quote }}
  {{- end }}
  {{- if $job.suspend }}
  suspend: true
  {{- end }}
  concurrencyPolicy: {{ $job.concurrencyPolicy | default "Forbid" }}
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: {{ $job.backoffLimit | default 2 }}
      {{- with $job.activeDeadlineSeconds }}
      activeDeadlineSeconds: {{ . }}
      {{- end }}
      template:
        metadata:
          labels:
            app: {{ $jobName }}
        spec:
          restartPolicy: Never
          {{- if $job.runAsUser }}
          securityContext:
            runAsUser: {{ $job.runAsUser }}
            runAsGroup: {{ $job.runAsGroup | default $job.runAsUser }}
          {{- end }}
          {{- with $job.imagePullSecrets }}
          imagePullSecrets:
            {{- range . }}
            - name: {{ . }}
            {{- end }}
          {{- end }}
          containers:
            - name: {{ .name }}
              image: {{ required (printf "%s: image must be set" $jobName) $job.image | quote }}
              {{- with $job.command }}
              command:
                {{- toYaml . | nindent 16 }}
              {{- end }}
              {{- with $job.args }}
              args:
                {{- toYaml . | nindent 16 }}
              {{- end }}
              {{- with $job.env }}
              env:
                {{- range $name, $value := . }}
                - name: {{ $name }}
                  value: {{ $value | quote }}
                {{- end }}
              {{- end }}
              {{- with $job.envFrom }}
              envFrom:
                {{- with .configMapRef }}
                - configMapRef:
                    name: {{ . }}
                {{- end }}
                {{- with .secretRef }}
                - secretRef:
                    name: {{ . }}
                {{- end }}
              {{- end }}
              {{- with $job.resources }}
              resources:
                {{- toYaml . | nindent 16 }}
              {{- end }}
              {{- with $job.pvcMounts }}
              volumeMounts:
                {{- range $claim, $path := . }}
                - name: {{ $claim }}
                  mountPath: {{ $path | quote }}
                {{- end }}
              {{- end }}
          {{- with $job.pvcMounts }}
          volumes:
            {{- range $claim, $_ := . }}
            - name: {{ $claim }}
              persistentVolumeClaim:
                claimName: {{ $appName }}-{{ $claim }}-pvc
            {{- end }}
          {{- end }}
{{- end }}
