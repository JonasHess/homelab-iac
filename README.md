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
- **Tautulli:** Plex monitoring and statistics.
-   **SABnzbd:** Usenet binary downloader.
- **qBittorrent:** Torrent Client.

**Home Automation:**

-   **Home Assistant:** Open-source home automation platform.
-   **Zigbee2MQTT:** Zigbee to MQTT gateway.
-   **Mosquitto:** MQTT broker for smart home devices.
-   **HomeMatic:** Home automation controller.

**Productivity & Utilities:**

-   **Paperless-ngx:** Document management system.
-   **Paperless-GPT:** An extension for paperless using LLMs for Document-Classification.
-   **Gotenberg:** API for converting HTML, Markdown, and Office documents to PDF.
-   **Tika:** Content detection and analysis framework.
-   **Ollama:** Run and deploy local LLMs.
-   **OpenWebUI:** Chat interface for Ollama.
- **Redis:** In memory data store.
-   **Postgres:** Database server.
-   **Duplicati:** Backup software.
-   **Stirling-PDF:** Local PDF manipulation tool.
-   **SFTP Go:** SFTP/FTP server.
- **AdGuard:** DNS ad-blocker
- **Cloudflare DDNS:** Dynamic DNS update for cloudflare
- **Duplicati-Prometheus-Exporter:** Prometheus metrics for duplicati

**Monitoring & Observability:**

-   **Prometheus:** Monitoring and alerting system.
-   **Grafana:** Data visualization and dashboards.
- **Alertmanager:** Alert handling for prometheus

**CI/CD & Development:**

-   **Drone:** Continuous Integration and Continuous Delivery (CI/CD) platform.
-   **GitHub ARC (Actions Runner Controller):** Self-hosted GitHub Actions runners.
- **ArgoCD:** GitOps management tool for Kubernetes.

**Other:**

-   **Immich:** Self-hosted photo and video backup solution.
-   **ASN (Archive Serial Number):** Small Nodejs App to redirect to Paperless via the asn.
-   **TGTG:** TooGoodToGo Notification tool.
- **CNPG:** Cloud-native PostgreSQL for managing postgres clusters

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