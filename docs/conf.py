from datetime import datetime


extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.extlinks',
]
templates_path = ['_templates']
source_suffix = '.rst'
master_doc = 'index'

year = datetime.now().year
project = u'ddtrace'
copyright = u'2016-{}, Datadog, Inc'.format(year)
author = u'Datadog, Inc'

exclude_patterns = [
    '_build',
    'Thumbs.db',
    '.DS_Store'
]

html_theme = 'alabaster'
html_sidebars = {
    '**': [
        'about.html',
        'nav.html',
        'relations.html',
        'searchbox.html',
    ]
}
html_theme_options = {
    'description': 'Datadog\'s Python tracing client',
    'fixed_sidebar': True,
}


extlinks = {
    'issue': ('https://github.com/sphinx-doc/sphinx/issues/%s', 'issue '),
    'setupdocs': (
        'https://docs.datadoghq.com/tracing/setup/python/%s', 'setupdocs'
    )
}
