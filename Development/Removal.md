# Removing an existing Omarchy installation

Use **Installation → Remove Omarchy…** while no installation or download is running. The app says what removal will do and asks for a macOS administrator account and password; that password and the **Remove Omarchy** button are the confirmation. There is no Return-key shortcut for the destructive button. The helper still requires the ticket's phrase (`delete omarchy installation and data`, or `return free space to macos`), which the app sends with the ticket, so older helpers keep working.

Removal uses the signed privileged helper directly. It does not download an OS,
engine, IPSW or catalog. The helper owns a five-minute, single-use plan; the client
sends an opaque ticket, never disk identifiers or commands. Cancellation performs
no disk mutation. Credentials are not written to the removal journal.

## Supported layout

The booted internal macOS physical store must precede one complete Omarchy group:
Apple APFS startup container (at most 4 GiB, exactly Omarchy System/Data and
Preboot/Recovery volumes), Omarchy EFI, Linux boot, Linux root. The four removal
partitions must be contiguous. Apple ISC must precede macOS and Apple Recovery
must follow Omarchy. Gaps immediately before and after Omarchy are reclaimed too.

Unknown partitions, multiple or partial installations, external boot, multiple
APFS physical stores, mismatched names/types/UUIDs and explicitly unsupported
`apple,j614s` are refused. This deliberately does not attempt general disk repair.

The group is confirmed as one installation by the ESP's `m1n1/boot.bin`,
`asahi/stub_info.json` (naming the startup container's volume group) and
`asahi/installer.log` (listing every partition it created), and by the startup
container's m1n1 boot object chainloading that ESP. The engine writes the two
`asahi/` records, each replaced whole, as soon as the ESP is laid out and before
Recovery staging, and deletes macOS's AppleDouble (`._*`) files from the ESP.

An install can stop before those records exist. Right after creating its
partitions, engines from `20261106` write a receipt beside their root-owned
journal (`<journal>.partitions.receipt`) naming the startup container, EFI, boot
and root by UUID, offset and size. When either `asahi/` record is missing and
exactly one unfinished journal (`stub_and_esp_started`, no `stub-and-esp-installed`
checkpoint, no completion) names this startup container in its
`apfs-target-prepared` evidence, the four partitions must be exactly the ones its
receipt names. That holds even when the payload download or write failed, before
the ESP or the startup container's m1n1 existed.

Journals of earlier engines (`20261022`, failing at Recovery staging,
#176/#177/#180) have no receipt. Only when both `asahi/` records are missing,
their startup container must sit at the start of the plan's approved extent with
the recorded size, all four partitions must lie inside that extent, and every
startup container check applies. That proves the startup container and, through
its m1n1, the ESP exactly; the Linux boot and root partitions only by their place
inside the extent this helper carved out. A receipt that is present but unreadable
disqualifies its journal. The helper reads journals through its root-owned
directory with `openat` and `O_NOFOLLOW`.

The helper validates disk/partition/container/volume identities before the first
write and after each operation. It deletes the startup container without a
replacement name (modern diskutil also deletes its physical-store partition),
then erases EFI/boot/root by GPT partition UUID. It grows the bound macOS store
with `diskutil apfs resizeContainer <macOS-store-UUID> 0`. Success requires the
expected size increase and unchanged Apple ISC, macOS identities and Recovery.

Apple's local `diskutil(8)` documentation was checked for deleteContainer,
resizeContainer and UUID device addressing. No force-delete fallback is used.

## Interrupted native Recovery staging

The pre-staging AppleDouble failure can leave the EFI proof files absent. Use the explicit [Resume Interrupted Installation flow](InterruptedResume.md) in an updated app and helper. It verifies the existing allocation and finishes staging without repeating partition or Linux-image writes. Successful recovery publishes a truthful verified-partition receipt that this removal flow accepts. Missing proof alone never authorizes deletion.

## Interrupted removal

The root-owned helper directory contains `removal-<ticket UUID>.json`. The plan
and next phase are written atomically, and the file and containing directory are
synchronized before each mutation. A journal not marked `complete` prevents new
installation/removal requests, including after a helper restart. Never delete or
mark that journal complete merely to bypass the block: reconcile its approved
UUIDs with the actual disk layout first. A failed resize can leave all Omarchy
partitions removed with the space still unallocated. Do not replay deletion.

Connection loss locks the app's further disk actions; it does not claim that the
helper stopped or that no writes occurred. A successful removal refreshes the
installer's inspection so an old installation plan cannot be reused.

Install journals are named by the plan's binding digest, and removal restores the layout that plan was made from. A successful removal therefore moves `execution-journals` to `retired-execution-journals/<removal ticket>` in the helper directory; otherwise a reinstall at the same size would find its earlier journal complete and report success without writing anything. A failed removal keeps them in place.

## Verification and simulator

In a debug `--simulate` run, choose the same menu action and use **Removal test** inside the popup. Cases: complete removal, no installation, unfamiliar/partial layout, helper unavailable, incorrect password, changed disk, deletion interrupted, macOS resize failed and connection lost. Simulation uses no helper, disks, passwords or downloads. A simulation blocked by an uncertain outcome has a separate **Reset simulation** action.

`OmarchyRemovalTests` exercises the real planner/executor with in-memory disk
operations, native plist parsing, every failed command position, journal failure,
protected identity drift, no-op growth, administrator rejection, exact phrase,
foreign/replayed tickets, interrupted-helper restart and concurrent install/removal
rejection. The running UI was checked for success and light/dark appearance. The typed confirmation phrase it was also checked for has since been removed from the app.

Physical removal has not been executed for this feature. The M1 Thunderbolt route
was unavailable during development. Before a physical test, deploy both the new
app and helper, check the current disk layout and obtain explicit authorization
for that removal run. Keep the app closed when preparing the M1 for manual use.
