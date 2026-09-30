#!/bin/sh
set -eu
umask 077

# The value is a public key, never a private credential or execution permit.
test -n "${SSH_PUBLIC_KEY:-}"
printf '%s\n' "$SSH_PUBLIC_KEY" | grep -Eq '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)) '
mkdir -p /root/.ssh /run/sshd
printf '%s\n' "$SSH_PUBLIC_KEY" > /root/.ssh/authorized_keys
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
ssh-keygen -A
exec /usr/sbin/sshd -D -e \
    -o PasswordAuthentication=no \
    -o KbdInteractiveAuthentication=no \
    -o PermitRootLogin=prohibit-password \
    -o AllowTcpForwarding=no \
    -o X11Forwarding=no
