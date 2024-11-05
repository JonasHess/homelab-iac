## Setup Instructions

### Copy Script
Copy `fix_homematic.sh` to `/usr/local/bin/`:

```bash
sudo cp fix_homematic.sh /usr/local/bin/
```

### Make Script Executable
Make the script executable:

```bash
sudo chmod +x /usr/local/bin/fix_homematic.sh
```

### Set Up Automatic Execution After Updates
Copy the service file `fix_homematic.service` to `/etc/systemd/system/`:

```bash
sudo cp fix_homematic.service /etc/systemd/system/
```

Enable the systemd service to run the script automatically after updates:

```bash
sudo systemctl enable fix_homematic.service
```

### Manual Execution
You can start the service manually if needed:

```bash
sudo systemctl start fix_homematic.service
```