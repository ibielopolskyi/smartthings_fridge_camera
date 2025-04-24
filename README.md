# SmartThings Family Hub Fridge Camera Integration for Home Assistant

This is a custom integration to output SmartThings Family Hub fridge camera feeds in [HomeAssistant](https://home-assistant.io).

<p float="left">
  <img src="./assets/presentation/dashboard-demo.png" width=600  alt="dashboard-demo"/>
</p>

**Please be aware that this implementation is a proof of concept. Don't expect everything to work!**

# Installation

When it comes to the installation, you have two options:
- Option 1: Install via HACS
- Option 2: Manual Installation

## Option 1: Install via HACS

First, navigate to the HACS tab on your Home Assistant instance. On this page, click the three dots in the top right corner and select "Custom repositories":
<p float="left">
  <img src="assets/install/install-step-1.png" width=1200  alt="dashboard-demo"/>
</p>


In the floating window, please enter the link to the repository and select "Integration" as the type. (Just copy the link from the browser as shown)
<p float="left">
  <img src="assets/install/install-step-2.png" width=600  alt="dashboard-demo"/>
  <img src="assets/install/install-step-2_1.png" width=600  alt="dashboard-demo"/>
</p>


After clicking the "Add" button, the repository should be added at the top as follows:
<p float="left">
  <img src="assets/install/install-step-3.png" width=1200  alt="dashboard-demo"/>
</p>


Next, search for your recently added repository in the HACS search bar and click on it:
<p float="left">
  <img src="assets/install/install-step-4.png" width=1200  alt="dashboard-demo"/>
</p>


Click the "Download" button in the bottom right:
<p float="left">
  <img src="assets/install/install-step-5.png" width=1200  alt="dashboard-demo"/>
</p>


Confirm the download of the latest version by clicking "Download". If everything works, you should see a success message afterwards:
<p float="left">
  <img src="assets/install/install-step-6.png" width=1200  alt="dashboard-demo"/>
</p>


### !!! Please restart Home Assistant for the changes to take effect !!!


### CONGRATULATIONS <3

You have successfully added the integration to your Home Assistant instance.


## Option 2: Manual Installation

Install it as you would do with any Home Assistant custom component:

1. Download the `custom_components` folder from the repository.
2. Copy the `samsung_familyhub_fridge` directory into the `custom_components` directory of your Home Assistant installation. The `custom_components` directory resides within your Home Assistant configuration directory.</br>
**Note**: if the `custom_components` directory does not exist, you need to create it.
After a correct installation, your configuration directory should look like the following:
    ```
    └── ...
    └── configuration.yaml
    └── custom_components
        └── samsung_familyhub_fridge
            └── __init__.py
            └── manifest.json
            └── api.py
            └── camera.py
            └── ...
    ```

For reference:
<p float="left">
  <img src="assets/install/install-step-manual-1.png" width=600  alt="dashboard-demo"/>
  <img src="assets/install/install-step-manual-2.png" width=600  alt="dashboard-demo"/>
</p>

### !!! Make sure to reboot Home Assistant after importing all files !!!


# Configuration

After the installation was successful, you can now configure the integration.

Navigate to "Settings" > "Devices & service":
<p float="left">
  <img src="assets/config/config-step-1.png" width=1200  alt="dashboard-demo"/>
</p>


Click "Add Integration" in the bottom right:
<p float="left">
  <img src="assets/config/config-step-2.png" width=1200  alt="dashboard-demo"/>
</p>


Search for the FamilyHub Integration you just downloaded and select it:
<p float="left">
  <img src="assets/config/config-step-3.png" width=1200  alt="dashboard-demo"/>
</p>


You need to enter your Smartthings Token and your Device ID. The token is used to access your SmartThings account. The device ID identifies your fridge.</br>
You can create a token from here: https://account.smartthings.com/tokens.</br>
And get your device ID from here: https://my.smartthings.com/advanced/devices.</br>
Click "Submit" to finish the setup:
<p float="left">
  <img src="assets/config/config-step-4.png" width=1200  alt="dashboard-demo"/>
</p>


If everything worked, you should see a success message:
<p float="left">
  <img src="assets/config/config-step-5.png" width=1200  alt="dashboard-demo"/>
</p>


Now let's add the camera to your dashboard. Navigate to your dashboard and add a card. Select the "Picture entity" card:
<p float="left">
  <img src="assets/config/config-step-6.png" width=1200  alt="dashboard-demo"/>
</p>


As the entity, you need to select your camera. You will see more than one camera entity. Just select the one that is working for you:
<p float="left">
  <img src="assets/config/config-step-7.png" width=1200  alt="dashboard-demo"/>
</p>


Make sure to select the additional settings as follows and click "Save":
<p float="left">
  <img src="assets/config/config-step-8.png" width=1200  alt="dashboard-demo"/>
</p>


Credits
-------

This integration was developed by [ibielopolskyi][ibielopolskyi].<br/>
HACS integration was added by [CurryPlayer][curryplayer].<br/>
Special thanks to [HalloTschuess][hallotschuess].<br/>

[ibielopolskyi]: https://github.com/ibielopolskyi
[curryplayer]: https://github.com/CurryPlayer
[hallotschuess]: https://github.com/HalloTschuess