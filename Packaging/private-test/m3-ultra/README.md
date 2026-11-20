# Mac Studio (M3 Ultra) test, stage 0: read-only check

This is a private test kit for the Mac Studio (M3 Ultra, 2025). Stage 0 only reads facts from your Mac and writes one zip file to your Desktop. It installs nothing. It does not change your disk, your settings or macOS.

The project has no M3 Ultra to test on. Your file lets us check, before any Linux code runs, that our reading of Apple's firmware matches a real machine.

## 1. Update macOS first

The test needs macOS 27.0.1 or later. Open System Settings, then General, then Software Update, and install every update. Restart. Apple menu, then About This Mac, must show 27.0.1 or later. The kit stops with a message if it does not.

## 2. Download and check the kit

You get a ZIP file and a SHA-256 line from us, in two separate messages. Open Terminal and run this, with the real file name:

```bash
shasum -a 256 ~/Downloads/omarchy-m3-ultra-stage0-kit.zip
```

The long number must be the same as in our message. If it is not, stop and tell us. Then double-click the ZIP to unpack it.

## 3. Run it

In Terminal, type `cd ` (with a space), drag the unpacked folder into the window, press Return, then run:

```bash
bash collect-stage0.sh
```

If your Mac is not a Mac Studio (M3 Ultra), or macOS is too old, the kit says so and collects nothing. Otherwise it takes a few seconds and ends with "Done", the path of a zip on your Desktop, and a SHA-256 line.

## 4. Send the result

Send us the zip and the SHA-256 line. The zip holds four text files: a short summary, a hardware list, GPU facts and the hardware tree. The kit removes the serial number, hardware UUID, unique chip ID, DRAM chip IDs and network addresses (Wi-Fi, Ethernet, Bluetooth). It also removes the name and user ID of the account that is logged in, any text you set for a lost Mac (the contact line on the lock screen), the names of programs that were running, the storage controller's unique ID, and the ID numbers of your volumes and disks. It holds none of your files. You can open the zip and read every file before you send it. Please do: search it for your name and your contact details first. The kit version is the first line of `summary.txt`.

## 5. Remove it

Delete the unpacked kit folder, the downloaded ZIP, and the `omarchy-m3-ultra-stage0-...` folder and zip on your Desktop. Nothing else was made.

## What can go wrong

Stage 0 changes nothing, so macOS and your data stay as they are. The worst case is that the kit stops with an error. Send us the exact text.

Later stages (starting Linux from a USB drive or an internal partition, then display, then GPU) are not part of this kit. Each will have its own guide, a full-backup step and clear stop rules. Do not follow any other instruction for those stages.
