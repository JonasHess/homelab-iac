
# Homematic
Installation guide
https://github.com/jens-maus/RaspberryMatic/wiki/Installation-Kubernetes

If you get the following error during the installation:
```bash
g1905:~$ sudo modprobe eq3_char_loop
modprobe: ERROR: could not insert 'eq3_char_loop': Exec format error
```

Try the following (adapt the kernel version to your version):

```bash
uname -a
sudo apt reinstall linux-headers-$(uname -r)
sudo apt reinstall pivccu-modules-dkms
sudo service pivccu-dkms start
sudo modprobe eq3_char_loop
```

If you get the following error during the start of the container, or you don't have internet connetion:
```bash
Identifying Homematic RF-Hardware: ...cat: can't open '/sys/class/net/eth0/address': No such file or directory
```

Adapt the Mac-Address in the `10-network.rules` file to the correct one. You can find the correct Mac-Address with the following commands:
```bash
ip link show
```

```bash
sudo nano /etc/udev/rules.d/10-network.rules
```

add the following line to the file:
```bash
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="9c:6b:00:20:1e:41", NAME="eth0"
```

Reloead the configuration and reboot the system:
```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo reboot
```

Maybe it needs some time till the application are reachable via the domains again.
I don't know why, but after some time it works again.