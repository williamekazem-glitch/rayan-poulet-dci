import os
import json
import httpx
import asyncio
from fastapi import FastAPI, Request, Response
from anthropic import Anthropic

app = FastAPI()
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
SUPERVISOR_NUMBER = os.environ.get("SUPERVISOR_NUMBER", "2250508316332")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

ADMIN_NUMBERS = {
    "2250710111118",   # Wallid
    "2250151636363",   # Poulet d'Ci Humain
}

ESCALADE_TRIGGER = "Je transmets votre demande"

# Historique des conversations en mémoire
conversation_history = {}

RAYAN_SYSTEM = """Tu es Rayan, l'assistante virtuelle de Poulet d'Ci.

Poulet d'Ci est une entreprise ivoirienne spécialisée dans tout ce qui concerne le poulet :
- Une ferme avicole : Poulet d'Ci produit sa propre volaille de qualité
- Un magasin de produits frais : poulet entier frais, poulet mariné, poulet découpé (blanc, cuisse, aile, gésier...), poulet prêt à cuisiner
- Domini Chap : le restaurant en ligne de Poulet d'Ci, qui propose uniquement des plats à base de poulet (sandwich poulet, poulet grillé, poulet braisé, poulet sauté, soupe de poulet, etc.)

Ton rôle :
- Accueillir chaleureusement les clients
- Répondre aux questions sur les produits, les prix, les disponibilités
- Prendre les commandes (magasin ou Domini Chap)
- Informer sur les horaires, la livraison, les points de vente
- Parler français, nouchi ou dioula selon comment le client s'exprime

Ton caractère : tu es souriante, dynamique, efficace. Tu représentes une marque moderne et professionnelle.

Si tu ne connais pas la réponse à une question (prix spécifique, stock, délai particulier), dis :
"Je transmets votre demande à notre équipe qui vous répondra rapidement."

Ne dépasse jamais 3 phrases par réponse sauf si le client pose une question qui nécessite plus de détails."""

async def get_admin_knowledge():
    if not SUPABASE_URL or not SUPABASE_KEY:
        return ""
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"{SUPABASE_URL}/rest/v1/admin_knowledge?select=info&order=created_at.desc&limit=50",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
            )
            if r.status_code == 200:
                items = r.json()
                return "\n".join([i["info"] for i in items]) if items else ""
    except:
        pass
    return ""

async def save_admin_knowledge(info: str):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{SUPABASE_URL}/rest/v1/admin_knowledge",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"},
                json={"info": info}
            )
            return r.status_code == 201
    except:
        return False

async def send_whatsapp(to: str, message: str):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": message}}
    async with httpx.AsyncClient() as c:
        await c.post(url, headers=headers, json=payload)

async def ask_rayan(sender: str, user_message: str) -> str:
    knowledge = await get_admin_knowledge()

    if sender not in conversation_history:
        conversation_history[sender] = []

    conversation_history[sender].append({"role": "user", "content": user_message})

    # Garder max 20 messages par conversation
    if len(conversation_history[sender]) > 20:
        conversation_history[sender] = conversation_history[sender][-20:]

    system = RAYAN_SYSTEM
    if knowledge:
        system += f"\n\nInformations mises à jour par l'équipe Poulet d'Ci :\n{knowledge}"

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system=system,
        messages=conversation_history[sender]
    )

    reply = response.content[0].text
    conversation_history[sender].append({"role": "assistant", "content": reply})
    return reply

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN and params.get("hub.mode") == "subscribe":
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    return Response(content="Forbidden", status_code=403)

@app.post("/webhook")
async def receive_message(request: Request):
    try:
        data = await request.json()
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return {"status": "ok"}

        msg = messages[0]
        sender = msg.get("from", "")
        msg_type = msg.get("type", "")

        # Message texte
        if msg_type == "text":
            text = msg.get("text", {}).get("body", "").strip()

            # Commande ADMIN
            if text.upper().startswith("ADMIN:") and sender in ADMIN_NUMBERS:
                info = text[6:].strip()
                saved = await save_admin_knowledge(info)
                if saved:
                    await send_whatsapp(sender, "✅ Information sauvegardée. Rayan en tiendra compte.")
                else:
                    await send_whatsapp(sender, "❌ Erreur lors de la sauvegarde.")
                return {"status": "ok"}

            # Message client normal
            reply = await ask_rayan(sender, text)
            await send_whatsapp(sender, reply)

            # Escalade si Rayan ne sait pas
            if ESCALADE_TRIGGER in reply and SUPERVISOR_NUMBER:
                notif = f"🐔 *Poulet d'Ci — Escalade Rayan*\nClient : {sender}\nQuestion : {text}\n\nRayan a transmis la demande."
                await send_whatsapp(SUPERVISOR_NUMBER, notif)

        # Localisation client
        elif msg_type == "location" and SUPERVISOR_NUMBER:
            loc = msg.get("location", {})
            lat = loc.get("latitude", "")
            lng = loc.get("longitude", "")
            await send_whatsapp(sender, "📍 Localisation reçue ! Notre équipe vous contacte pour la livraison.")
            await send_whatsapp(SUPERVISOR_NUMBER, f"📍 *Localisation client*\nNuméro : {sender}\nhttps://maps.google.com/?q={lat},{lng}")

    except Exception as e:
        print(f"Erreur webhook : {e}")

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "Rayan — Poulet d'Ci est en ligne 🐔"}
