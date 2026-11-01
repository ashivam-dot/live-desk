# Going live: the owner's steps

Everything else is built and runs on its own. These are the steps only the channel owner can do. They need
the owner's accounts and the stream keys, and the stream keys must never pass through anyone else.

## 1. Turn on live streaming (once per channel, takes 24 hours)

Do this in each channel's own Chrome profile: Orbitwire in Profile 5 (aksha.shivam18@gmail.com), Pal Pal News
in Profile 6 (aksh.shivam17@gmail.com).

1. Open <https://studio.youtube.com>, click **Create** > **Go live**.
2. Follow the prompts (phone verification if asked). YouTube enables live streaming after about 24 hours.

## 2. Oracle Cloud account (free)

1. Sign up at <https://signup.cloud.oracle.com> with **aksha.shivam18@gmail.com**. Pick home region **Frankfurt** or
   **London** (cannot be changed later).
2. Oracle asks for a card only to verify identity. Stay on the **Always Free** tier and never click "Upgrade to
   paid"; nothing is charged.

## 3. Stream settings and keys (after the 24 hours)

In each channel's Studio, **Create** > **Go live** > **Stream** tab:

- Title. Orbitwire: `Orbitwire LIVE: world news 24/7, headlines, earthquakes and every story`.
  Pal Pal: `पल पल न्यूज़ LIVE: 24 घंटे ताज़ा हिंदी खबरें`.
- Category **News & Politics**, audience "No, it's not made for kids".
- Stream settings: **Enable auto-start** on, **Enable auto-stop off** (so a short restart never ends the live),
  DVR on, latency Normal.
- Copy the **Stream key** (keep it to yourself).

## 4. Create the server (about 5 minutes)

Oracle Console > **Compute** > **Instances** > **Create instance**:

1. Image: **Canonical Ubuntu 24.04** (the aarch64 one). Shape: **Ampere** > **VM.Standard.A1.Flex**, 4 OCPU, 24 GB.
   If it says "out of capacity", try another availability domain, or 2 OCPU / 12 GB.
2. Networking: leave the defaults (a public IPv4 address is needed for outgoing traffic).
3. SSH keys: "No SSH keys" is fine.
4. **Show advanced options** > **Management** > **Paste cloud-init script**: paste the contents of
   [`server/cloud-init.yaml`](server/cloud-init.yaml), replacing `PASTE_ORBITWIRE_STREAM_KEY_HERE` and
   `PASTE_PALPAL_STREAM_KEY_HERE` with the two keys.
5. **Create**. After about 10 minutes both channels show "Excellent connection" in Studio's live control room.

The server pulls code updates on its own every 30 minutes. New Shorts reach the stream within about 90 minutes of
publishing: the librarian collects them hourly, and the server checks every 30 minutes.

## Keeping it running

- The stream restarts itself if anything fails. To stop one channel, change its stream key in Studio
  ("Reset" the key) or stop the instance in Oracle.
- Oracle can reclaim Always Free machines that sit idle for 7 days; the live encoding keeps this one busy.
