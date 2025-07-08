import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG = False
ROOT_URLCONF = "urls"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR,
        ],
    }
]
SECRET_KEY = ("SECRET",)
ALLOWED_HOSTS = ["*"]
