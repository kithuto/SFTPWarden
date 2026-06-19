# Troubleshooting

## Runtime Is Not Running

Start it:

```bash
docker compose up -d
```

Then refresh:

```bash
sftpwarden refresh -c dev
```

## Remote Checks Fail

Verify:

```bash
ssh deploy@example.com true
ssh deploy@example.com 'docker compose version'
```

