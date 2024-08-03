const functions = require("firebase-functions");
const admin = require("firebase-admin");
const OpenAI = require("openai");
const twilio = require("twilio");

// Initialize Firestore and Twilio
admin.initializeApp();
const db = admin.firestore();
const client = twilio(process.env.TWILIO_ACCOUNT_SID, process.env.TWILIO_AUTH_TOKEN);

// Initialize OpenAI client
const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
});

// Define Firestore collections
const USERS_COLLECTION = "users";
const MESSAGES_COLLECTION = "messages";

// Helper function to save messages to Firestore
async function saveMessage(phoneNumber, message, sender) {
  try {
    await db.collection(MESSAGES_COLLECTION).add({
      phone_number: phoneNumber,
      message: message,
      sender: sender,
      timestamp: admin.firestore.FieldValue.serverTimestamp(),
    });
  } catch (error) {
    console.error("Error saving message to Firestore:", error);
  }
}

// Helper function to get recent messages from Firestore
async function getRecentMessages(phoneNumber, limit = 3) {
  try {
    const messagesRef = db.collection(MESSAGES_COLLECTION)
      .where("phone_number", "==", phoneNumber)
      .orderBy("timestamp", "desc")
      .limit(limit);
    
    const snapshot = await messagesRef.get();
    return snapshot.docs.map(doc => doc.data());
  } catch (error) {
    console.error("Error retrieving messages from Firestore:", error);
    return [];
  }
}

// Main function to handle incoming SMS
exports.smsWebhook = functions.https.onRequest(async (req, res) => {
  const phoneNumber = req.body.From;
  const incomingMessage = req.body.Body.trim().toLowerCase();

  // Validate the incoming request
  if (!phoneNumber || !incomingMessage) {
    res.status(400).send("Invalid request");
    return;
  }

  // Check if the user is opted-in
  const userRef = db.collection(USERS_COLLECTION).doc(phoneNumber);
  const userDoc = await userRef.get();
  let optedIn = false;

  if (userDoc.exists) {
    optedIn = userDoc.data().opted_in;
  }

  // If the user is not opted-in, send opt-in message
  if (!optedIn) {
    if (["yes", "yeah", "y-e-a-h"].includes(incomingMessage)) {
      try {
        await userRef.set({ opted_in: true }, { merge: true });
        await client.messages.create({
          body: "How can I help you?",
          from: process.env.TWILIO_PHONE_NUMBER,
          to: phoneNumber,
        });
      } catch (error) {
        console.error("Error updating Firestore or sending SMS:", error);
      }
    } else if (incomingMessage === "stop") {
      try {
        await userRef.set({ opted_in: false }, { merge: true });
      } catch (error) {
        console.error("Error updating Firestore:", error);
      }
    } else {
      try {
        await client.messages.create({
          body: "Thank you for texting us. We're happy to help. Reply YES to opt-in. Reply STOP to stop.",
          from: process.env.TWILIO_PHONE_NUMBER,
          to: phoneNumber,
        });
      } catch (error) {
        console.error("Error sending SMS:", error);
      }
    }
    return res.sendStatus(200);
  }

  // Save user message to Firestore
  await saveMessage(phoneNumber, incomingMessage, "user");

  // Get recent messages for context
  const recentMessages = await getRecentMessages(phoneNumber);

  // Prepare messages for OpenAI API call
  const messagesForAI = recentMessages.map(msg => ({
    role: msg.sender === "user" ? "user" : "assistant",
    content: [{ type: "text", text: msg.message }]
  }));

  messagesForAI.push({
    role: "user",
    content: [{ type: "text", text: incomingMessage }]
  });

  // Call OpenAI API
  try {
    const response = await openai.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [
        {
          role: "system",
          content: [{ type: "text", text: "You are a homeless assistant" }]
        },
        ...messagesForAI,
      ],
      temperature: 1,
      max_tokens: 9399,
      top_p: 1,
      frequency_penalty: 0,
      presence_penalty: 0,
    });

    const aiMessage = response.choices[0].message.content[0].text;

    // Save AI response to Firestore
    await saveMessage(phoneNumber, aiMessage, "assistant");

    // Send AI response to the user via Twilio
    await client.messages.create({
      body: aiMessage,
      from: process.env.TWILIO_PHONE_NUMBER,
      to: phoneNumber,
    });

  } catch (error) {
    console.error("Error calling OpenAI API or sending SMS:", error);
  }

  res.sendStatus(200);
});

