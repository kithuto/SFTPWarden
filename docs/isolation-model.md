# Isolation Model

Each user is chrooted into:

```text
/data/<username>/
  upload/
```

The chroot directory is owned by root, and the upload directory is owned by the user UID/GID.

This is OpenSSH chroot isolation inside a container. It is not VM-grade isolation and does not replace host hardening.

