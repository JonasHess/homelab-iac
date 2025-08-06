# Infrastructure as Code for Home Lab

This repository contains the Infrastructure as Code configuration for a home lab environment. It leverages Kubernetes, Helm, and other tools to automate the deployment and management of various services and applications.

## Overview

This project aims to create a self-hosted, automated home lab environment. It employs best practices for configuration management, security, and ease of deployment. The infrastructure is defined declaratively, allowing for reproducible and consistent setups.

## Core Technologies

-   **Kubernetes:** The container orchestration platform that runs all the services.
-   **Helm:** The package manager for Kubernetes, used to define and manage applications.
-   **Akeyless:** Secure vault for storing secrets and credentials.
-   **Traefik:** A modern HTTP reverse proxy and load balancer.
- **ArgoCD:** A declarative, GitOps continuous delivery tool for Kubernetes.
-   **Cloudflare:** Used for DNS management and Dynamic DNS.
-   **ZFS:** File system for data storage with encryption, compression, and deduplication.
- **External Secrets Operator:** Used for accessing akeyless secrets.

## Deployed Applications and Services

This infrastructure currently manages the deployment of the following applications and services:

**Media & Entertainment:**

-   **Plex:** Media server for streaming movies and TV shows.
-   **Radarr:** Movie collection manager.
-   **Sonarr:** TV show collection manager.
-   **Readarr:** Ebook collection manager.
-   **Tautulli:** Plex monitoring and statistics.
-   **SABnzbd:** Usenet binary downloader.
-   **qBittorrent:** Torrent Client.
-   **Overseerr:** Request management and media discovery tool.
-   **Prowlarr:** Indexer manager for the *arr stack.
-   **Audiobookshelf:** Self-hosted audiobook and podcast server.

**Home Automation:**

-   **Home Assistant:** Open-source home automation platform.
-   **Zigbee2MQTT:** Zigbee to MQTT gateway.
-   **Mosquitto:** MQTT broker for smart home devices.
-   **HomeMatic:** Home automation controller.
-   **SmartHome3:** Smart home management system.

**Productivity & Utilities:**

-   **Paperless-ngx:** Document management system.
-   **Paperless-GPT:** An extension for paperless using LLMs for Document-Classification.
-   **Gotenberg:** API for converting HTML, Markdown, and Office documents to PDF.
-   **Tika:** Content detection and analysis framework.
-   **Ollama:** Run and deploy local LLMs.
-   **OpenWebUI:** Chat interface for Ollama.
-   **Redis:** In memory data store.
-   **Postgres:** Database server.
-   **Stirling-PDF:** Local PDF manipulation tool.
-   **SFTP Go:** SFTP/FTP server.
-   **AdGuard:** DNS ad-blocker.
-   **Cloudflare DDNS:** Dynamic DNS update for cloudflare.
-   **Duplicati-Prometheus-Exporter:** Prometheus metrics for duplicati.
-   **Vaultwarden:** Self-hosted Bitwarden server written in Rust.
-   **Mealie:** Recipe manager and meal planner.
-   **Radicale:** CalDAV and CardDAV server.
-   **Homer:** Static homepage dashboard.

**Monitoring & Observability:**

-   **Prometheus:** Monitoring and alerting system.
-   **Grafana:** Data visualization and dashboards.
-   **Alertmanager:** Alert handling for prometheus.

**CI/CD & Development:**

-   **ArgoCD:** GitOps management tool for Kubernetes.

**Backup & Storage:**

-   **Backrest:** Web UI and orchestrator for restic backup.
-   **Restic:** Fast, secure, and efficient backup program.

**Infrastructure & Platform:**

-   **Akeyless:** Secrets management platform.
-   **External Secrets Operator:** Kubernetes operator for external secret management.
-   **Traefik:** Modern HTTP reverse proxy and load balancer.
-   **Crossplane:** Infrastructure as code using Kubernetes.
-   **AWS Controllers:** Kubernetes controllers for AWS services.
-   **Reloader:** Kubernetes controller to watch changes in ConfigMap and Secrets.
-   **Profilarr:** Media library profiler and optimizer.

**Other:**

-   **Immich:** Self-hosted photo and video backup solution.
-   **ASN (Archive Serial Number):** Small Nodejs App to redirect to Paperless via the asn.
-   **TGTG:** TooGoodToGo Notification tool.
-   **Generic:** Base chart for generic applications.
-   **Whoami:** Simple HTTP service for testing.
-   **Test:** Test application deployment.

## Dependency Management with Renovate

This repository uses [Renovate](https://renovatebot.com/) for automated dependency updates. Renovate helps keep all dependencies up-to-date by automatically creating pull requests when new versions are available.

### Configuration Highlights:

- **Schedule**: Updates run at 3pm on Sundays (Europe/Berlin timezone)
- **Auto-merge**: Enabled for patch and minor updates across all dependency types
- **Manual approval**: Required for all major version updates
- **Supported dependency types**:
  - Helm charts
  - Docker images
  - Python packages
  - JavaScript/Node.js packages
  - Go modules
  - GitHub Actions
- **Custom regex managers**: Configured to detect and update:
  - ArgoCD Application targetRevisions
  - Docker image references in YAML files

The full Renovate configuration can be found in `.renovaterc.json`.

## TODOs & Future Improvements

Here are some planned improvements and tasks:

- \[ \] **Restart Pod argocd-server:** Automate the restart of the ArgoCD
  server pod to handle race conditions related to Akeyless secrets.
- \[ \] **Apps in Own Namespaces:** Migrate applications to their own
  namespaces (e.g., drone).
- \[ \] **Loki:** Add support for Loki (log aggregation system).
- \[ \] **Watchtower / Argo Image Updater:** Implement automatic image updates
  for containers.
- \[ \] **SAMBA:** Integrate SAMBA file sharing.
- \[ \] **Setup Monitoring for all services**
- \[ \] **Create all missing folders of pvs**
- \[ \] **Gateway API:** Implement a gateway API instead of IngressRoute
- \[ \] **Crossplane:** Use Crossplane for managing cloud resources.
- \[ \] **AWS Lambda:** Implement AWS Lambda functions for specific tasks.
- \[ \] **CalDav:** Add CalDAV server for calendar synchronization.
- \[ \] **Smarthome:** make it Kubernetes API ready