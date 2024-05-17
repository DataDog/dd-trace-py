import openai


openai.api_key = "william.conti@datadoghq.com"
openai.api_base = "https://openai-api-proxy.us1.staging.dog/v1"

with open("chatgpt_prompt.txt", "r") as file:
    text = file.read()
    prompt = text

response = openai.ChatCompletion.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.7,
    top_p=1,
    frequency_penalty=0,
    presence_penalty=0,
)

# Format response as markdown
markdown_response = response.choices[0].message.content

# Write markdown response to file\n
with open("chatgpt_response.md", "w") as file:
    file.write(markdown_response)
