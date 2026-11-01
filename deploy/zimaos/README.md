# PhotoSift on ZimaOS

GitHub Actions tests every push to `main`, then publishes `ghcr.io/snehithgit/photosift:latest`
for x86-64. The image holds the API, the web app and the worker. The GPU laptop still runs the
model service and agent, and pulls work from Zima.

## Install

1. In [docker-compose.yml](docker-compose.yml), change `/DATA/Gallery` in **both** services to your
   photo folder. Leave `:ro` on it, so originals can't be written to from the container.
2. In ZimaOS, open App Store, choose Custom Install, and import that file.
3. Set `PHOTOSIFT_AGENT_TOKEN` to a long random secret.
4. Open `http://<zima-address>:8000` and choose `/library`, or a folder inside it, from the list.
5. When you're ready, click **Scan now** under Library. Nothing is read until you do.

To start over, use Settings, Libraries, Clear database. It keeps your originals, exported copies,
libraries, settings, prompts, Teach labels and model setup.

App data lives in `/DATA/AppData/photosift`. Keep it on a local disk: SQLite must not run on a
network share. The app has no login, so keep port 8000 on your home network and off the internet.

## Connect the laptop

The laptop needs no file share. For each job it downloads that one original from Zima, using the
same token, and checks its hash. In `agent.local.json` on the laptop:

- set `serverUrl` to `http://<zima-address>:<port>`
- put the same secret in a local file and point `tokenFile` at it
- leave `rootMappings` empty

The token and photos travel over plain HTTP, so keep this on your trusted home network.
`--init-local` is only for a server on the same machine, so don't use it here. The rest of the
setup is in the [agent guide](../../agent/README.md).

## Not tested yet

CI runs the tests on Linux and smoke-tests the image, but this has not run on a real ZimaOS machine.
Folder permissions, downloading photos from a real Zima, and speed on Zima's disks and CPU are
unverified.
