# DoLu for Home Assistant

*[Leer en español](README.es.md)*

A custom integration that connects Home Assistant to the DoLu backend.

It exists to remove the last manual step of the installation: creating a long-lived access
token by hand in Home Assistant and copying it into the backend's `.env`. Instead, the
backend announces itself on the network, Home Assistant discovers it, and a single-use code
pairs the two.

Panels still reach Home Assistant over MQTT, exactly as before. This integration doesn't
touch them, and it **creates no entities and no devices**: the backend already publishes the
"DoLu backend" device with its link sensor over MQTT, and a second device by the same name
would only leave you wondering which one to look at.

## What it does, in one line

It creates an admin user in Home Assistant called **DoLu**, issues a long-lived token for
it, and hands that token to the backend over an encrypted, verified channel. You copy
nothing.

## Requirements

- Home Assistant **2026.3** or newer (before that, custom integrations can't ship their own
  brand images).
- DoLu backend **0.3.36** or newer.
- Home Assistant and the backend on the same local network. mDNS doesn't cross routers; if
  they're on different networks, adding the backend by IP still works.

## Installation

### HACS

Add it as a custom repository (HACS → overflow menu → Custom repositories), category
*Integration*, and download it. Then **restart Home Assistant**: until you do, Home
Assistant doesn't know the integration exists and can't discover anything.

### Manually

Copy `custom_components/dolu/` into your Home Assistant configuration directory (the one
with `configuration.yaml`) and restart.

## Usage

1. The backend shows up under **Settings → Devices & services** as discovered. If it
   doesn't, add it by hand using its IP address.
2. Generate a pairing code in the DoLu admin panel, under **Management → Home Assistant**.
   It is shown once and lasts ten minutes.
3. Type the code into Home Assistant.
4. Compare the fingerprint it then shows against the one in the DoLu panel, pair by pair,
   and confirm.

Confirming creates the **DoLu** user and hands over its token. From then on the backend
talks to Home Assistant on its own.

### Why the steps are in that order

The order isn't arbitrary. What eventually changes hands is admin access to your home, so
the side that has to do the authenticating is Home Assistant — and that decides everything:

- **The certificate is pinned before a word is exchanged.** The very first request already
  demands the certificate the backend announced. Someone who copies that announcement never
  reaches the first screen: their certificate is a different one.
- **The code is never sent.** It's used to make the backend *prove* it knows it. If it
  can't, Home Assistant stops without having sent anything at all.
- **The fingerprint is shown last, not first**, and it's the fingerprint of the certificate
  that actually served the connection. Comparing it against the panel is the final barrier —
  the one left if someone read your code over your shoulder. That's why it's the same ten
  pairs DoLu shows: you read the two screens side by side.

Worth knowing: if you mistype the code, Home Assistant catches it **without asking the
backend**, so a typo doesn't burn any of the five attempts that lock the code. That limit is
there for someone sending proofs at the backend, not for someone fumbling a keyboard.

## Finding and revoking DoLu's access

This is the most important part of this README, because it's what lets you undo everything
without anyone's help.

### Where it isn't

**Don't go looking for the token in your profile.** The *Long-lived access tokens* list
under Settings → your avatar → Security shows only the tokens belonging to **the user you're
signed in as**, and DoLu's token belongs to a separate user. It will never appear there.
Nothing is missing — it just isn't yours.

### Where it is

**Settings → People → Users**, where you'll find a user named **DoLu**:

- It's an **administrator**, because writing states and firing events in Home Assistant
  requires it. There's no middle permission that would do.
- **Local access only** is switched on, because the backend always lives on your network.
- It **cannot sign in**: it's created without a password or any credentials. It exists only
  to hold the token.

### How to revoke it

| What you want | What to do |
| --- | --- |
| Cut off access now | Delete the **DoLu** user under Settings → People. The token dies with it |
| Cut it off without deleting | Deactivate the user. Home Assistant drops all of its tokens |
| Remove everything, cleanly | Delete the integration under Settings → Devices & services. That revokes the token, deletes the user, and tells the backend to forget the link |

If you revoke access without removing the integration, Home Assistant notices by itself: the
integration is flagged as needing attention and offers to pair again. The DoLu panel says so
too, and both routes lead to the same place.

### When access stops working on its own

There are **two** possible causes, and from the outside they look identical:

1. **The token was revoked** — someone deleted or deactivated the DoLu user, or took away
   its administrator role. Pair again and you're done.
2. **The backend stopped talking from a local address.** The DoLu user has "Local access
   only" switched on, so a misrouted VPN, a NAT presenting a public address, or a reverse
   proxy sitting in front of Home Assistant all produce **exactly the same rejection**.
   Pairing again will not fix this one — it would work until the next restart. Fix it in the
   network, or clear "Local access only" on the DoLu user.

## Privacy and scope

- The token lives on the backend, in a file with `0600` permissions. It is never written to
  a log and never shown on a screen.
- The integration **sends nothing outside your network**: it only talks to the backend, at
  its local address, with its certificate pinned.
- It creates no entities, no devices and no automations.

## Development

Brand images live in `custom_components/dolu/brand/`: `icon.png` and `icon@2x.png` with the
dark D for light themes, `dark_icon.png` and `dark_icon@2x.png` with the white D for dark
themes. Home Assistant serves the dark variants from the local directory just like the light
ones, and falls back to `icon.png` if any is missing.

To test against a throwaway Home Assistant (a container with its own configuration
directory), copy the integration directory over and restart it:

```bash
rsync -av --delete --exclude __pycache__ \
    custom_components/dolu/ USER@HOST:CONFIG_PATH/custom_components/dolu/
ssh USER@HOST docker restart CONTAINER_NAME
```

Two details that bite:

- The `--delete` points at the integration directory only. Against the whole configuration
  directory, it would wipe Home Assistant.
- The `--exclude __pycache__` isn't cosmetic: Home Assistant writes there from inside the
  container, where it runs as root, and without the exclusion `--delete` fails trying to
  remove files that aren't its own.

Every change needs a Home Assistant restart — an integration's modules are imported once —
but the log level can be raised live, from Developer tools → Actions:

```yaml
action: logger.set_level
data:
  custom_components.dolu: debug
```

And one warning that saves an afternoon: **the browser caches translations**. A key added in
a freshly deployed version won't show up until a hard reload (`Ctrl+Shift+R`), and a missing
key doesn't raise an error — it renders empty, or as the raw key. If a string looks wrong,
reload before assuming it's broken. Formatting failures *are* visible: Home Assistant picks
them up in its own log under `frontend.js.modern`.

### Testing the pairing

`scripts/e2e_pair_test.py` exercises `custom_components/dolu/pairing.py` — the very file
that runs inside the flow — against a real backend, from inside the Home Assistant
container, which is where `aiohttp` and `homeassistant` live:

```bash
docker exec CONTAINER_NAME python3 /config/e2e_pair_test.py \
    --host BACKEND_ADDRESS --port 3000 --fp FINGERPRINT --code XXXX-XXXX
```

Besides the two endpoints, it checks the thing that breaks silently: that
`scrypt(code, salt)` and the HMAC transcript come out identical in Python and in Node. When
those two drift apart, the symptom is "wrong code" with the right code.

Testing the certificate barrier needs a real impostor — a second HTTPS server with its own
certificate, announcing the real backend's fingerprint — because a pairing that works proves
nothing about whether the pinning does.

## License

MIT. See [LICENSE](LICENSE).
