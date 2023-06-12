import re


CONFIDENTIAL_REGEXP = [
    {
        "Category": "PII",
        "Description": "Email",
        "Regex": r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,4}$",
        "Risk": "1",
    },
    {
        "Category": "PII",
        "Description": "US Social Security number",
        "Regex": r"^([0-6]\d{2}|7[0-6]\d|77[0-2])([ \-]?)(\d{2})\2(\d{4})$",
        "Risk": "7",
    },
    # source of next items: https://stackoverflow.com/questions/9315647/regex-credit-card-number-tests
    {
        "Category": "PII",
        "Description": "Mastercard",
        "Regex": r"^(5[1-5][0-9]{14}|2(22[1-9][0-9]{12}|2[3-9][0-9]{13}|[3-6][0-9]{14}|7[0-1][0-9]{13}|720[0-9]{12}))$",
        "Risk": "8",
    },
    {
        "Category": "PII",
        "Description": "Visa Card",
        "Regex": r"^4[0-9]{12}(?:[0-9]{3})?$",
        "Risk": "8",
    },
    # source of next items: https://github.com/datumbrain/aws-macie-pii-confidential-regexes/blob/master/regex_list.csv
    {
        "Category": "Confidential",
        "Description": "Arista network configuration",
        "Regex": "via\\ \\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3},\\ " "\\d{2}:\\d{2}:\\d{2}",
        "Risk": "7",
    },
    {
        "Category": "PII",
        "Description": "BBVA Compass Routing Number - California",
        "Regex": "^321170538$",
        "Risk": "1",
    },
    {
        "Category": "PII",
        "Description": "Bank of America Routing Numbers - California",
        "Regex": "^(?:121|026)00(?:0|9)(?:358|593)$",
        "Risk": "1",
    },
    {
        "Category": "Confidential",
        "Description": "Box Links",
        "Regex": "https://app.box.com/[s|l]/\\S+",
        "Risk": "3",
    },
    {"Category": "PII", "Description": "CVE Number", "Name": "CVE Number", "Regex": "CVE-\\d{4}-\\d{4,7}", "Risk": "3"},
    {
        "Category": "PII",
        "Description": "California Drivers License",
        "Regex": "^[A-Z]{1}\\d{7}$",
        "Risk": "1",
    },
    {
        "Category": "PII",
        "Description": "Chase Routing Numbers - California",
        "Regex": "^322271627$",
        "Risk": "1",
    },
    {
        "Category": "Confidential",
        "Description": "Cisco Router Config",
        "Regex": "service\\ timestamps\\ [a-z]{3,5}\\ datetime\\ "
        "msec|boot-[a-z]{3,5}-marker|interface\\ "
        "[A-Za-z0-9]{0,10}[E,e]thernet",
        "Risk": "9",
    },
    {
        "Category": "PII",
        "Description": "Citibank Routing Numbers - California",
        "Regex": "^32(?:11|22)71(?:18|72)4$",
        "Risk": "1",
    },
    {
        "Category": "Confidential",
        "Description": "DSA Private Key",
        "Regex": "-----BEGIN DSA PRIVATE " "KEY-----(?:[a-zA-Z0-9\\+\\=\\/\"']|\\s)+?-----END DSA PRIVATE " "KEY-----",
        "Risk": "8",
    },
    {
        "Category": "PII",
        "Description": "Dropbox Links",
        "Regex": "https://www.dropbox.com/(?:s|l)/\\S+",
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "EC Private Key",
        "Regex": "-----BEGIN (?:EC|ECDSA) PRIVATE "
        "KEY-----(?:[a-zA-Z0-9\\+\\=\\/\"']|\\s)+?-----END (?:EC|ECDSA) "
        "PRIVATE KEY-----",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "Encrypted DSA Private Key",
        "Regex": "-----BEGIN DSA PRIVATE KEY-----\\s.*,ENCRYPTED(?:.|\\s)+?-----END " "DSA PRIVATE KEY-----",
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "Encrypted EC Private Key",
        "Regex": "-----BEGIN (?:EC|ECDSA) PRIVATE "
        "KEY-----\\s.*,ENCRYPTED(?:.|\\s)+?-----END (?:EC|ECDSA) PRIVATE "
        "KEY-----",
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "Encrypted Private Key",
        "Regex": "-----BEGIN ENCRYPTED PRIVATE KEY-----(?:.|\\s)+?-----END ENCRYPTED " "PRIVATE KEY-----",
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "Encrypted PuTTY SSH DSA Key",
        "Regex": "PuTTY-User-Key-File-2: ssh-dss\\s*Encryption: " "aes(?:.|\\s?)*?Private-MAC:",
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "Encrypted RSA Private Key",
        "Regex": "-----BEGIN RSA PRIVATE KEY-----\\s.*,ENCRYPTED(?:.|\\s)+?-----END " "RSA PRIVATE KEY-----",
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "Google Application Identifier",
        "Regex": "[0-9]+-\\w+.apps.googleusercontent.com",
        "Risk": "2",
    },
    {
        "Category": "Confidential",
        "Description": "HIPAA PHI National Drug Code",
        "Regex": "^\\d{4,5}-\\d{3,4}-\\d{1,2}$",
        "Risk": "2",
    },
    {
        "Category": "Confidential",
        "Description": "Huawei config file",
        "Regex": "sysname\\ HUAWEI|set\\ authentication\\ password\\ simple\\ huawei",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "Individual Taxpayer Identification Numbers (ITIN)",
        "Regex": "^9\\d{2}(?:[ \\-]?)[7,8]\\d(?:[ \\-]?)\\d{4}$",
        "Risk": "4",
    },
    {
        "Category": "Confidential",
        "Description": "John the Ripper",
        "Regex": "[J,j]ohn\\ [T,t]he\\ [R,r]ipper|john-[1-9].[1-9].[1-9]|Many\\ "
        "salts:|Only\\ one\\ "
        "salt:|openwall.com/john/|List.External:[0-9a-zA-Z]*|Loaded\\ "
        "[0-9]*\\ password hash|guesses:\\ \\d*\\ \\ time:\\ "
        "\\d*:\\d{2}:\\d{2}:\\d{2}|john\\.pot",
        "Risk": "1",
    },
    {
        "Category": "Confidential",
        "Description": "KeePass 1.x CSV Passwords",
        "Regex": '"Account","Login Name","Password","Web Site","Comments"',
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "KeePass 1.x XML Passwords",
        "Regex": "<pwlist>\\s*?<pwentry>[\\S\\s]*?<password>[\\S\\s]*?<\\/pwentry>\\s*?<\\/pwlist>",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "Large number of US Phone Numbers",
        "Regex": "\\d{3}-\\d{3}-\\d{4}|\\(\\d{3}\\)\\ ?\\d{3}-?\\d{4}",
        "Risk": "1",
    },
    {
        "Category": "Confidential",
        "Description": "Large number of US Zip Codes",
        "Regex": "^(\\d{5}-\\d{4}|\\d{5})$",
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "Lightweight Directory Access Protocol",
        "Regex": "(?:dn|cn|dc|sn):\\s*[a-zA-Z0-9=, ]*",
        "Risk": "2",
    },
    {
        "Category": "Confidential",
        "Description": "Metasploit Module",
        "Regex": "require\\ 'msf/core'|class\\ Metasploit|include\\ " "Msf::Exploit::\\w+::\\w+",
        "Risk": "6",
    },
    {
        "Category": "Confidential",
        "Description": "MySQL database dump",
        "Regex": "DROP DATABASE IF EXISTS(?:.|\\n){5,300}CREATE "
        "DATABASE(?:.|\\n){5,300}DROP TABLE IF EXISTS(?:.|\\n){5,300}CREATE "
        "TABLE",
        "Risk": "7",
    },
    {
        "Category": "Confidential",
        "Description": "MySQLite database dump",
        "Regex": "DROP\\ TABLE\\ IF\\ EXISTS\\ \\[[a-zA-Z]*\\];|CREATE\\ TABLE\\ " "\\[[a-zA-Z]*\\];",
        "Risk": "7",
    },
    {
        "Category": "Confidential",
        "Description": "Network Proxy Auto-Config",
        "Regex": "proxy\\.pac|function\\ FindProxyForURL\\(\\w+,\\ \\w+\\)",
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "Nmap Scan Report",
        "Regex": "Nmap\\ scan\\ report\\ for\\ [a-zA-Z0-9.]+",
        "Risk": "7",
    },
    {
        "Category": "Confidential",
        "Description": "PGP Header",
        "Regex": "-{5}(?:BEGIN|END)\\ PGP\\ MESSAGE-{5}",
        "Risk": "5",
    },
    {
        "Category": "Confidential",
        "Description": "PGP Private Key Block",
        "Regex": "-----BEGIN PGP PRIVATE KEY BLOCK-----(?:.|\\s)+?-----END PGP " "PRIVATE KEY BLOCK-----",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "PKCS7 Encrypted Data",
        "Regex": "(?:Signer|Recipient)Info(?:s)?\\ ::=\\ "
        "\\w+|[D|d]igest(?:Encryption)?Algorithm|EncryptedKey\\ ::= \\w+",
        "Risk": "5",
    },
    {
        "Category": "Confidential",
        "Description": "Password etc passwd",
        "Regex": "[a-zA-Z0-9\\-]+:[x|\\*]:\\d+:\\d+:[a-zA-Z0-9/\\- " '"]*:/[a-zA-Z0-9/\\-]*:/[a-zA-Z0-9/\\-]+',
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "Password etc shadow",
        "Regex": "[a-zA-Z0-9\\-]+:(?:(?:!!?)|(?:\\*LOCK\\*?)|\\*|(?:\\*LCK\\*?)|"
        "(?:\\$.*\\$.*\\$.*?)?):\\d*:\\d*:\\d*:\\d*:\\d*:\\d*:",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "PlainText Private Key",
        "Regex": "-----BEGIN PRIVATE KEY-----(?:.|\\s)+?-----END PRIVATE KEY-----",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "PuTTY SSH DSA Key",
        "Regex": "PuTTY-User-Key-File-2: ssh-dss\\s*Encryption: " "none(?:.|\\s?)*?Private-MAC:",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "PuTTY SSH RSA Key",
        "Regex": "PuTTY-User-Key-File-2: ssh-rsa\\s*Encryption: " "none(?:.|\\s?)*?Private-MAC:",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "Public Key Cryptography System (PKCS)",
        "Regex": 'protocol="application/x-pkcs[0-9]{0,2}-signature"',
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "Public encrypted key",
        "Regex": "-----BEGIN PUBLIC KEY-----(?:.|\\s)+?-----END PUBLIC KEY-----",
        "Risk": "1",
    },
    {
        "Category": "Confidential",
        "Description": "RSA Private Key",
        "Regex": "-----BEGIN RSA PRIVATE " "KEY-----(?:[a-zA-Z0-9\\+\\=\\/\"']|\\s)+?-----END RSA PRIVATE " "KEY-----",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "SSL Certificate",
        "Regex": "-----BEGIN CERTIFICATE-----(?:.|\\n)+?\\s-----END CERTIFICATE-----",
        "Risk": "3",
    },
    {
        "Category": "PII",
        "Description": "SWIFT Codes",
        "Regex": "[A-Za-z]{4}(?:GB|US|DE|RU|CA|JP|CN)[0-9a-zA-Z]{2,5}$",
        "Risk": "4",
    },
    {
        "Category": "Confidential",
        "Description": "Samba Password config file",
        "Regex": "[a-z]*:\\d{3}:[0-9a-zA-Z]*:[0-9a-zA-Z]*:\\[U\\ \\]:.*",
        "Risk": "7",
    },
    {
        "Category": "Confidential",
        "Description": "Simple Network Management Protocol Object Identifier",
        "Regex": "(?:\\d\\.\\d\\.\\d\\.\\d\\.\\d\\.\\d{3}\\.\\d\\.\\d\\.\\d\\.\\d\\.\\d\\.\\d\\.\\d\\."
        "\\d\\.\\d{4}\\.\\d)|[a-zA-Z]+[)(0-9]+\\.[a-zA-Z]+[)(0-9]+\\.[a-zA-Z]+[)(0-9]+\\.[a-zA-Z]+[)(0-9]+"
        "\\.[a-zA-Z]+[)(0-9]+\\.[a-zA-Z]+[)(0-9]+\\.[a-zA-Z0-9)(]+\\.[a-zA-Z0-9)(]+\\.[a-zA-Z0-9)(]+\\.[a-zA-Z0-9)(]+",
        "Risk": "5",
    },
    {
        "Category": "Confidential",
        "Description": "Slack 2FA Backup Codes",
        "Regex": "Two-Factor\\s*\\S*Authentication\\s*\\S*Backup\\s*\\S*Codes(?:.|\\n)*[Ss]lack(?:.|\\n)*\\d{9}",
        "Risk": "8",
    },
    {
        "Category": "PII",
        "Description": "UK Drivers License Numbers",
        "Regex": "[A-Z]{5}\\d{6}[A-Z]{2}\\d{1}[A-Z]{2}",
        "Risk": "4",
    },
    {
        "Category": "PII",
        "Description": "UK Passport Number",
        "Regex": "\\d{10}GB[RP]\\d{7}[UMF]{1}\\d{9}",
        "Risk": "1",
    },
    {
        "Category": "PII",
        "Description": "USBank Routing Numbers - California",
        "Regex": "^12(?:1122676|2235821)$",
        "Risk": "1",
    },
    {
        "Category": "PII",
        "Description": "United Bank Routing Number - California",
        "Regex": "^122243350$",
        "Risk": "1",
    },
    {
        "Category": "PII",
        "Description": "Wells Fargo Routing Numbers - California",
        "Regex": "^121042882$",
        "Risk": "1",
    },
    {
        "Category": "Confidential",
        "Description": "aws_access_key",
        "Regex": "((access[-_]?key[-_]?id)|(ACCESS[-_]?KEY[-_]?ID)|"
        "([Aa]ccessKeyId)|(access[_-]?id)).{0,20}AKIA[a-zA-Z0-9+/]{16}[^a-zA-Z0-9+/]",
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "aws_credentials_context",
        "Regex": "access_key_id|secret_access_key|AssetSync.configure",
        "Risk": "3",
    },
    {
        "Category": "Confidential",
        "Description": "aws_secret_key",
        "Regex": "((secret[-_]?access[-_]?key)|(SECRET[-_]?ACCESS[-_]?KEY|(private[-_]?key))|"
        "([Ss]ecretAccessKey)).{0,20}[^a-zA-Z0-9+/][a-zA-Z0-9+/]{40}\\b",
        "Risk": "10",
    },
    {
        "Category": "Confidential",
        "Description": "facebook_secret",
        "Regex": "(facebook_secret|FACEBOOK_SECRET|facebook_app_secret|FACEBOOK_APP_SECRET)[a-z_ "
        "=\\s\"'\\:]{0,5}[^a-zA-Z0-9][a-f0-9]{32}[^a-zA-Z0-9]",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "github_key",
        "Regex": "(GITHUB_SECRET|GITHUB_KEY|github_secret|github_key|github_token|GITHUB_TOKEN|"
        "github_api_key|GITHUB_API_KEY)[a-z_ "
        "=\\s\"'\\:]{0,10}[^a-zA-Z0-9][a-zA-Z0-9]{40}[^a-zA-Z0-9]",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "google_two_factor_backup",
        "Regex": "(?:BACKUP VERIFICATION CODES|SAVE YOUR BACKUP " "CODES)[\\s\\S]{0,300}@",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "heroku_key",
        "Regex": "(heroku_api_key|HEROKU_API_KEY|heroku_secret|HEROKU_SECRET)[a-z_ "
        "=\\s\"'\\:]{0,10}[^a-zA-Z0-9-]\\w{8}(?:-\\w{4}){3}-\\w{12}[^a-zA-Z0-9\\-]",
        "Risk": "7",
    },
    {
        "Category": "Confidential",
        "Description": "microsoft_office_365_oauth_context",
        "Regex": "https://login.microsoftonline.com/common/oauth2/v2.0/token|"
        "https://login.windows.net/common/oauth2/token",
        "Risk": "1",
    },
    {
        "Category": "Confidential",
        "Description": "pgSQL Connection Information",
        "Regex": "(?:postgres|pgsql)\\:\\/\\/",
        "Risk": "2",
    },
    {
        "Category": "Confidential",
        "Description": "slack_api_key",
        "Regex": "(slack_api_key|SLACK_API_KEY|slack_key|SLACK_KEY)[a-z_ "
        "=\\s\"'\\:]{0,10}[^a-f0-9][a-f0-9]{32}[^a-f0-9]",
        "Risk": "7",
    },
    {
        "Category": "Confidential",
        "Description": "slack_api_token",
        "Regex": "(xox[pb](?:-[a-zA-Z0-9]+){4,})",
        "Risk": "8",
    },
    {
        "Category": "Confidential",
        "Description": "ssh_dss_public",
        "Regex": "ssh-dss [0-9A-Za-z+/]+[=]{2}",
        "Risk": "1",
    },
    {
        "Category": "Confidential",
        "Description": "ssh_rsa_public",
        "Regex": "ssh-rsa AAAA[0-9A-Za-z+/]+[=]{0,3} [^@]+@[^@]+",
        "Risk": "1",
    },
]


TYPE_REGEXP = [
    {"Description": "empty", "Regex": "^$"},
    {"Description": "numeric", "Regex": "^[0-9]+$"},
    {"Description": "alpha", "Regex": "^[A-Za-z]+$"},
    {"Description": "hex", "Regex": "^[A-Fa-f0-9]+$"},
    {"Description": "alphanumeric", "Regex": "^[A-Za-z0-9]+$"},
    {"Description": "base64", "Regex": "^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"},
    {"Description": "ascii", "Regex": "^[\x01-\x7E]+$"},
]


def metadata(value):
    res = {}
    for elt in CONFIDENTIAL_REGEXP:
        m = re.match(elt["Regex"], value)
        if m:
            res[elt["Category"]] = elt["Description"]
            break
    for elt in TYPE_REGEXP:
        m = re.match(elt["Regex"], value)
        if m:
            res["Type"] = elt["Description"]
            break
    return res
