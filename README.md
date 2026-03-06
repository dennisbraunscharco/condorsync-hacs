# CondorSync Home Assistant Integration

This custom integration allows you to view your CondorSync devices in Home Assistant.

## Features
- **Device Status**: Monitor if your devices are online or offline.
- **Auto-Discovery**: Automatically fetches all devices associated with your account.
- **Easy Setup**: Use the Home Assistant UI to configure the integration.

## Installation via HACS

1. Ensure [HACS](https://hacs.xyz/) is installed.
2. In HACS, go to **Integrations**.
3. Click the three dots in the top right corner and select **Custom repositories**.
4. Add the URL of this repository: `https://github.com/dennisbraunscharco/condorsync-hacs.git`
5. Select **Integration** as the category.
6. Click **Add**.
7. Find **CondorSync** in HACS and click **Download**.
8. Restart Home Assistant.

## Configuration

1. In Home Assistant, go to **Settings** -> **Devices & Services**.
2. Click **Add Integration**.
3. Search for **CondorSync**.
4. Enter your CondorSync email and password.
5. (Optional) Change the API URL if you are using a custom backend.
