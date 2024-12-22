# Duplicati Metric Export Settings

## Default Options

Options added here are applied to all backups, but can be overridden in each individual backup.

### Options

#### `send-http-any-operation`
By default, messages will only be sent after a backup operation. Use this option to send messages for all operations.

- **Default value:** `"false"`

#### `send-http-result-output-format`
Use this option to select the output format for results. Available formats: `Duplicati`, `Json`.

- **Default value:** `"Duplicati"`

#### `send-http-url`
Use this option to set an HTTP report URL.

- **Default value:** `""`

Example:
```text
--send-http-url=http://duplicati-prometheus-exporter-service:5000/
--send-http-result-output-format=Json
--send-http-any-operation=true
```