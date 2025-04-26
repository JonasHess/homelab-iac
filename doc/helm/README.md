## render Helm template
### Render a Helm template with multiple values files

```bash
helm template my-release . -f /mnt/c/Users/MZimmermann/IdeaProjects/homelab-iac/apps/homer/values.yaml -f /mnt/c/Users/MZimmermann/IdeaProjects/homelab-iac/base-chart/values/michael-values.yaml -f /mnt/c/Users/MZimmermann/IdeaProjects/homelab-iac/base-chart/values.yaml --set generic.appName=homer
```