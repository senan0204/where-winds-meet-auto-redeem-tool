# Where Winds Meet Auto Redeem Tool by Senan

A Windows utility for redeeming batches of Where Winds Meet gift codes. Paste a list, test the first code, then run the batch. The tool enters and submits each code in the game and records the results in its Activity log.

## Download

Download **Auto Redeem Tool.exe** from [GitHub Releases](../../releases/latest) and double-click it. The portable EXE bundles Python.

## How to use

1. Set the game to **Windowed** or **Borderless**.
2. Open **Settings → Other** and leave the **Exchange Code** row visible with the code entry dialog closed.
3. In the tool, click **Open codes.yar.gg**. On the website, open **⋮ More tools**, choose **Copy all active codes**, and confirm. Return to the app and click **Paste**.
4. Click **Test first code only**. Switch back to the game during the countdown, which defaults to five seconds. The app also tries to bring the game to the foreground.
5. Check the **Activity log**. If the test worked, click **Start redeeming** and switch back to the game during the countdown.
6. Press **F12** or click **Stop (F12)** at any time to cancel the run.

If the game runs as administrator, launch the tool with the same permissions so Windows can deliver its simulated input.

## Build from source

Use Windows Python 3.11 with Tkinter. Run `python redeem_bot.py` to open the app from source.

```powershell
python -m pip install pyinstaller==6.22.3
python -m PyInstaller --onefile --windowed --name Senan-Redeemer-Guide --add-data "icon.ico;." --icon icon.ico redeem_bot.py
```

The English interface and Senan edition changes were made on November 25, 2026. This project is based on [Yanyun Code Autofill v1.1.0](https://github.com/yunyi-zhao/yanyun-code-autofill/releases/tag/v1.1.0) by Dazhu Titi, commit `bef9785`. See [LICENSE](LICENSE) for GNU GPL v3 terms.
