# 🎬 Jellyfin Cinema Poster

![Project Status](https://img.shields.io/badge/status-active-brightgreen)
![Python](https://img.shields.io/badge/python-3.8%2B-blue)
![Jellyfin](https://img.shields.io/badge/jellyfin-compatible-purple)

A beautiful, immersive digital signage solution for your Home Theater. This application turns any vertical monitor into a **dynamic digital poster** that cycles through your Jellyfin library and automatically switches to a "Now Playing" mode when a movie starts.

Designed with a premium "Cinema Grade" aesthetic, featuring smooth animations, metadata badges, and deep immersion.

---

## ✨ Features

* **🔄 Smart Carousel:** Randomly cycles through movies in your library with a smooth "Ken Burns" zoom effect.
* **⚡ Now Playing Detection:** Automatically interrupts the carousel when you start watching a movie.
    * Displays a dedicated "NOW PLAYING" header with an animated equalizer.
    * Shows a live progress bar synced with your playback.
    * Displays start/end times and formatted duration.
* **📱 Vertical Optimization:** Specifically designed for 9:16 vertical monitors (Digital Posters), but works on standard screens too.
* **🎨 Immersive UI:**
    * High-quality backdrop blurs.
    * Edge-to-edge progress bars.
    * High-contrast typography (Bebas Neue & Montserrat).
    * Metadata badges (4K/1080p, Audio codec, Year, Rating).
* **🤖 Zero Config User:** Automatically detects the primary user on your Jellyfin server.

---

## 📸 Screenshots

| Idle Mode (Carousel) |
|:---:|
| ![Carousel](./screenshots/screen.jpg) |

---

## 🛠️ Prerequisites

* **Python 3.8+** installed on your machine.
* A running **Jellyfin Server**.
* A Jellyfin **API Key**.

### How to get your API Key
1.  Open your Jellyfin Dashboard.
2.  Go to **Advanced** > **API Keys**.
3.  Click **+** to create a new key.
4.  Name it "CinemaPoster" and copy the generated key.

---

## 🚀 Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/simoneluconi/jellyfin-poster.git
    cd jellyfin-poster
    ```

2.  **Install dependencies:**
    It is recommended to use a virtual environment.
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configuration:**
    Rename the example environment file to `.env`:
    ```bash
    mv .env.example .env
    ```
    
    Open `.env` with a text editor and fill in your details:
    ```ini
    JELLYFIN_URL=your_jellyfin_url_here # Your Jellyfin IP and Port
    JELLYFIN_API_KEY=your_long_api_key_here
    PORT=5000
    DEBUG=False
    ```

---

## ▶️ Usage

1.  **Start the server:**
    ```bash
    python app.py
    ```

2.  **Open the display:**
    Open a web browser on the device connected to your vertical monitor and navigate to:
    `http://localhost:5000` (or your PC's IP address).

3.  **Kiosk Mode (Full Screen):**
    * **Chrome/Edge:** Press `F11` to enter full screen.
    * **Auto-start tip:** You can launch Chrome in kiosk mode via command line:
        ```bash
        chrome.exe --kiosk http://localhost:5000 --incognito
        ```

---

## 📂 Project Structure

```text
jellyfin-poster/
├── app.py              # Main Python/Flask backend logic
├── requirements.txt    # Python dependencies
├── .env                # Private configuration (Excluded from Git)
├── .env.example        # Public configuration template
└── templates/
    └── poster.html     # The Frontend (HTML/CSS/JS)