from datetime import datetime


extensions = [
    'sphinx.ext.autodoc',
]
templates_path = ['_templates']
source_suffix = '.rst'
master_doc = 'index'

project = u'ddtrace'
copyright = u'2018, Datadog, Inc'
author = u'Datadog, Inc'
year = datetime.now().year

exclude_patterns = [
    '_build',
    'Thumbs.db',
    '.DS_Store'
]

html_theme = 'alabaster'
html_sidebars = {
    '**': [
        'about.html',
        'navigation.html',
        'relations.html',
        'searchbox.html',
        'donate.html',
    ]
}
html_theme_options = {
    'description': 'Datadog\'s Python tracing client',
    'github_user': 'Datadog',
    'github_repo': 'dd-trace-py',
    'fixed_sidebar': True,
}
