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

# 预设采用官方 YuE2 仓库示例（multimodal-art-projection/YuE · examples/song.json，City Lights）；
# 官方仅提供此一组 style+lyrics 案例，不再混入自拟条目。
STYLE_PRESETS = [
    {
        "id": "city_lights",
        "name": "City Lights（官方示例）",
        "style": "English, warm piano pop, expressive female voice, acoustic piano, rounded bass and light drums, lyrical memorable melody, unhurried phrasing, 88 BPM",
    },
]

LYRIC_PRESETS = [
    {
        "id": "city_lights",
        "name": "City Lights（官方示例）",
        "lyrics": "[Verse]\nNeon fades along the lane\nFootsteps keep the time of rain\nFold the night and leave it here\nMorning has a sky to clear\n\n[Chorus]\nLet the day come into view\nEvery road begins with you\nHold a little room for light\nWe will sing beyond the night",
    },
]
