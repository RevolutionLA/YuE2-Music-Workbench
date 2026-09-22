class Settings:
    app_host: str = "127.0.0.1"  # 仅本机使用：不暴露到局域网（如需 LAN 访问改回 0.0.0.0 并配套加鉴权）
    app_port: int = 7863

    audiocpp_base_url: str = "http://127.0.0.1:8080"
    audiocpp_timeout_sec: float = 86400.0  # 24h：不限时长，生成多久等多久
    audiocpp_autostart: bool = True
    audiocpp_bin: str = "cpp/audiocpp_server.exe"
    open_browser: bool = True
    audiocpp_config: str = "cpp/server.json"
    audiocpp_ready_timeout_sec: float = 90.0
    audiocpp_model_id: str = "yue2"
    audiocpp_family: str = "yue2"

    warmup_on_start: bool = False

    default_cot: str = "full"
    default_seed: int = 831001
    default_cfg_scale: float = 1.0
    default_num_inference_steps: int = 32
    default_abc_temperature: float = 0.7
    default_abc_top_p: float = 0.9
    default_abc_top_k: int = 30
    default_semantic_temperature: float = 1.0
    default_semantic_top_p: float = 0.95
    default_semantic_top_k: int = 100

    voices_dir: str = "runtime/voices"
    sensevoice_model_dir: str = "py312/SenseVoiceSmall"
    sensevoice_device: str = "cpu"
    sensevoice_language: str = "auto"
    gtcrn_ckpt: str = "scripts/model_trained_on_dns3.tar"
    gtcrn_device: str = "cpu"


settings = Settings()

COT_MODES = ["full", "melody", "off"]

STYLE_PRESETS = [
    {
        "id": "piano_pop",
        "name": "温暖钢琴流行",
        "style": "English, warm piano pop, expressive female voice, acoustic piano, rounded bass and light drums, lyrical memorable melody, unhurried phrasing, 88 BPM",
    },
    {
        "id": "indie_pop",
        "name": "明亮独立流行",
        "style": "English, indie pop, bright acoustic guitar, soft drums, warm lead vocal, polished demo mix, 104 BPM",
    },
    {
        "id": "c_pop",
        "name": "华语流行",
        "style": "Chinese, contemporary C-pop, clear female vocal, clean electric guitar, tight drums, warm bass, radio mix, 96 BPM",
    },
    {
        "id": "folk",
        "name": "民谣弹唱",
        "style": "Chinese, acoustic folk, intimate male vocal, fingerstyle guitar, light shaker, close-mic demo, 82 BPM",
    },
    {
        "id": "rock",
        "name": "流行摇滚",
        "style": "English, pop rock, bright guitars, clean drums, warm vocal, live-band energy, 118 BPM",
    },
    {
        "id": "jazz",
        "name": "灵魂爵士",
        "style": "English, soulful jazz-funk, smoky female vocal, Rhodes, walking bass, brush drums, late-night club mix, 92 BPM",
    },
    {
        "id": "ballad",
        "name": "钢琴抒情",
        "style": "Chinese, intimate piano ballad, soft female vocal, sparse piano, subtle strings, emotional phrasing, 72 BPM",
    },
    {
        "id": "jpop",
        "name": "J-Pop",
        "style": "Japanese, bright J-pop, energetic female vocal, sparkling synths, punchy drums, glossy mix, 128 BPM",
    },
]

LYRIC_PRESETS = [
    {
        "id": "city_lights",
        "name": "City Lights",
        "lyrics": "[Verse]\nNeon fades along the lane\nFootsteps keep the time of rain\nFold the night and leave it here\nMorning has a sky to clear\n\n[Chorus]\nLet the day come into view\nEvery road begins with you\nHold a little room for light\nWe will sing beyond the night",
    },
    {
        "id": "sunrise",
        "name": "Sunrise",
        "lyrics": "[Verse]\nSoft morning light is touching the window.\nI hear the city waking below.\n\n[Chorus]\nStay with the rhythm, let it carry us home.\nSing with the sunrise, we are never alone.",
    },
    {
        "id": "last_train",
        "name": "末班车",
        "lyrics": "[Verse]\n站台灯还亮着最后一排\n夜风把票根吹到鞋边\n你说下个夏天还会回来\n我把这句话叠进衣袋\n\n[Chorus]\n末班车带走未说完的话\n城市在雨里慢慢长大\n如果黎明会把路点亮\n我们就在下一站相见吧",
    },
]
