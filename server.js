const express = require("express");
const http = require("http");
const { Server } = require("socket.io");
const { Pool } = require("pg");
const path = require("path");
const bcrypt = require("bcryptjs");
const crypto = require("crypto");

const app = express();
const server = http.createServer(app);
const io = new Server(server, { maxHttpBufferSize: 120e6 });
const PORT = process.env.PORT || 10000;

// Database connection sanity check
const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
  console.error("❌ CRITICAL ERROR: process.env.DATABASE_URL is missing!");
  process.exit(1);
}

const pool = new Pool({
  connectionString: connectionString,
  ssl: { rejectUnauthorized: false }
});

app.use(express.json({ limit: "120mb" }));
app.use(express.urlencoded({ extended: true, limit: "120mb" }));
app.use(express.static(path.join(__dirname, "public")));

const sessions = new Map();
const activeUsers = new Map();

const VIP_PHONE = process.env.VIP_PHONE || "0920689815";
const VIP_USERNAME = process.env.VIP_USERNAME || "@kalifa";

async function q(sql, params = []) {
  return (await pool.query(sql, params)).rows;
}

function safeUser(u) {
  if (!u) return null;
  const { password, ...x } = u;
  return x;
}

function token() {
  return crypto.randomBytes(32).toString("hex");
}

async function initDB() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS users(
      id SERIAL PRIMARY KEY,
      username VARCHAR(50) UNIQUE NOT NULL,
      password TEXT NOT NULL,
      phone VARCHAR(30),
      bio TEXT,
      avatar TEXT,
      cover TEXT,
      full_name VARCHAR(120),
      gender VARCHAR(30),
      city VARCHAR(100),
      is_admin BOOLEAN DEFAULT FALSE,
      is_vip BOOLEAN DEFAULT FALSE,
      banned_until TIMESTAMP NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS posts(
      id SERIAL PRIMARY KEY,
      user_id INT REFERENCES users(id) ON DELETE CASCADE,
      username VARCHAR(50),
      content TEXT,
      media_url TEXT,
      media_type VARCHAR(30),
      approved BOOLEAN DEFAULT FALSE,
      rejected BOOLEAN DEFAULT FALSE,
      likes INT DEFAULT 0,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS comments(
      id SERIAL PRIMARY KEY,
      post_id INT REFERENCES posts(id) ON DELETE CASCADE,
      user_id INT REFERENCES users(id) ON DELETE CASCADE,
      username VARCHAR(50),
      comment TEXT NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS post_likes(
      post_id INT REFERENCES posts(id) ON DELETE CASCADE,
      user_id INT REFERENCES users(id) ON DELETE CASCADE,
      PRIMARY KEY(post_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS follows(
      follower_id INT REFERENCES users(id) ON DELETE CASCADE,
      following_id INT REFERENCES users(id) ON DELETE CASCADE,
      PRIMARY KEY(follower_id,following_id)
    );

    CREATE TABLE IF NOT EXISTS friend_requests(
      id SERIAL PRIMARY KEY,
      sender_id INT REFERENCES users(id) ON DELETE CASCADE,
      receiver_id INT REFERENCES users(id) ON DELETE CASCADE,
      status VARCHAR(20) DEFAULT 'pending',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS messages(
      id SERIAL PRIMARY KEY,
      sender_id INT REFERENCES users(id) ON DELETE CASCADE,
      receiver_id INT REFERENCES users(id) ON DELETE CASCADE,
      body TEXT,
      media_url TEXT,
      media_type VARCHAR(30),
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      seen BOOLEAN DEFAULT FALSE
    );

    CREATE TABLE IF NOT EXISTS notifications(
      id SERIAL PRIMARY KEY,
      user_id INT REFERENCES users(id) ON DELETE CASCADE,
      type VARCHAR(40),
      title TEXT,
      body TEXT,
      data JSONB,
      seen BOOLEAN DEFAULT FALSE,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS missed_calls(
      id SERIAL PRIMARY KEY,
      caller_id INT REFERENCES users(id) ON DELETE CASCADE,
      receiver_id INT REFERENCES users(id) ON DELETE CASCADE,
      caller VARCHAR(50),
      receiver VARCHAR(50),
      call_type VARCHAR(20),
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      seen BOOLEAN DEFAULT FALSE
    );

    CREATE TABLE IF NOT EXISTS saved_posts(
      user_id INT REFERENCES users(id) ON DELETE CASCADE,
      post_id INT REFERENCES posts(id) ON DELETE CASCADE,
      PRIMARY KEY(user_id,post_id)
    );
  `);
  console.log("Neon database tables ready.");
}

initDB().catch(e => console.error("DB INIT ERROR", e));

async function auth(req, res, next) {
  const t = (req.headers.authorization || "").replace("Bearer ", "");
  const uid = sessions.get(t);
  if (!uid) return res.status(401).json({ success: false, message: "Login required" });

  const users = await q("SELECT * FROM users WHERE id=$1", [uid]);
  if (!users.length) return res.status(401).json({ success: false, message: "Account not found" });

  const u = users[0];
  if (u.banned_until && new Date(u.banned_until) > new Date()) {
    return res.status(403).json({ success: false, message: "Account suspended" });
  }

  req.user = u;
  req.token = t;
  next();
}

function adminOnly(req, res, next) {
  if (!req.user?.is_admin) {
    return res.status(403).json({ success: false, message: "Admin access required" });
  }
  next();
}

async function notify(userId, type, title, body, data = {}) {
  const rows = await q(
    `INSERT INTO notifications (user_id,type,title,body,data) VALUES($1,$2,$3,$4,$5) RETURNING *`,
    [userId, type, title, body, JSON.stringify(data)]
  );

  for (const [sid, u] of activeUsers) {
    if (u.userId === userId) {
      io.to(sid).emit("notification", rows[0]);
    }
  }
}

app.get("/api/config", (req, res) => {
  res.json({ vipPhone: VIP_PHONE, vipUsername: VIP_USERNAME });
});

// User & Admin Signup
app.post("/api/auth/signup", async (req, res) => {
  try {
    const { username, password, phone, bio, full_name, gender, city, avatar, admin_code } = req.body;
    if (!username || !password) {
      return res.status(400).json({ success: false, message: "Username fi password guuti" });
    }

    const hash = await bcrypt.hash(password, 12);
    // Code 'ADMIN123' akka admin-itti akka galmaa'aniif
    const isAdmin = admin_code === "ADMIN123" || username === process.env.ADMIN_USERNAME;

    const rows = await q(
      `INSERT INTO users (username,password,phone,bio,full_name,gender,city,avatar,is_admin) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *`,
      [username.trim(), hash, phone || "", bio || "", full_name || "", gender || "", city || "", avatar || "", isAdmin]
    );

    const t = token();
    sessions.set(t, rows[0].id);
    res.json({ success: true, token: t, user: safeUser(rows[0]) });
  } catch (e) {
    res.status(400).json({
      success: false,
      message: e.code === "23505" ? "Username'n duraan jira" : "Signup failed"
    });
  }
});

app.post("/api/auth/login", async (req, res) => {
  try {
    const rows = await q("SELECT * FROM users WHERE username=$1", [req.body.username]);
    if (!rows.length) return res.status(400).json({ success: false, message: "Username hin jiru" });

    const u = rows[0];
    if (u.banned_until && new Date(u.banned_until) > new Date()) {
      return res.status(403).json({ success: false, message: "Account keessan yeroo muraasaaf cufameera." });
    }

    if (!(await bcrypt.compare(req.body.password, u.password))) {
      return res.status(400).json({ success: false, message: "Password dogoggora" });
    }

    const t = token();
    sessions.set(t, u.id);
    res.json({ success: true, token: t, user: safeUser(u) });
  } catch (e) {
    res.status(500).json({ success: false, message: "Server error" });
  }
});

app.post("/api/auth/logout", auth, (req, res) => {
  sessions.delete(req.token);
  res.json({ success: true });
});

app.get("/api/me", auth, (req, res) => {
  res.json({ success: true, user: safeUser(req.user) });
});

app.put("/api/profile", auth, async (req, res) => {
  const { phone, bio, avatar, cover, full_name, gender, city } = req.body;
  const rows = await q(
    `UPDATE users SET phone=$1, bio=$2, avatar=$3, cover=$4, full_name=$5, gender=$6, city=$7 WHERE id=$8 RETURNING *`,
    [phone || "", bio || "", avatar || "", cover || "", full_name || "", gender || "", city || "", req.user.id]
  );
  res.json({ success: true, user: safeUser(rows[0]) });
});

app.get("/api/users", auth, async (req, res) => {
  const rows = await q(`SELECT id,username,avatar,full_name,is_vip FROM users ORDER BY username LIMIT 500`);
  const online = new Set([...activeUsers.values()].map(x => x.userId));
  res.json(rows.map(x => ({ ...x, isOnline: online.has(x.id) })));
});

app.get("/api/posts", auth, async (req, res) => {
  const rows = await q(
    `SELECT p.*,u.avatar,u.full_name FROM posts p JOIN users u ON u.id=p.user_id WHERE p.approved=TRUE AND p.rejected=FALSE ORDER BY p.id DESC LIMIT 100`
  );
  res.json(rows);
});

app.post("/api/posts", auth, async (req, res) => {
  const { content, media_url, media_type } = req.body;
  const rows = await q(
    `INSERT INTO posts (user_id,username,content,media_url,media_type,approved) VALUES($1,$2,$3,$4,$5,FALSE) RETURNING id`,
    [req.user.id, req.user.username, content || "", media_url || null, media_type || null]
  );
  // Postiin erga galmeessamee booda maammilli "Post submitted" qofa arga, approval-iin admin jala jira
  res.json({ success: true, message: "Postii keessan ergameera! Qulqullinaaf erga ilaalamee booda public ta'a." });
});

app.post("/api/posts/:id/like", auth, async (req, res) => {
  const pid = Number(req.params.id);
  const exists = await q(`SELECT 1 FROM post_likes WHERE post_id=$1 AND user_id=$2`, [pid, req.user.id]);
  if (exists.length) {
    await q(`DELETE FROM post_likes WHERE post_id=$1 AND user_id=$2`, [pid, req.user.id]);
    await q(`UPDATE posts SET likes=GREATEST(likes-1,0) WHERE id=$1`, [pid]);
  } else {
    await q(`INSERT INTO post_likes (post_id,user_id) VALUES($1,$2)`, [pid, req.user.id]);
    await q(`UPDATE posts SET likes=likes+1 WHERE id=$1`, [pid]);
  }
  res.json({ success: true });
});

app.get("/api/posts/:id/comments", auth, async (req, res) => {
  res.json(await q(`SELECT * FROM comments WHERE post_id=$1 ORDER BY id ASC`, [req.params.id]));
});

app.post("/api/posts/:id/comments", auth, async (req, res) => {
  const rows = await q(
    `INSERT INTO comments (post_id,user_id,username,comment) VALUES($1,$2,$3,$4) RETURNING *`,
    [req.params.id, req.user.id, req.user.username, String(req.body.comment || "").slice(0, 1000)]
  );
  res.json({ success: true, comment: rows[0] });
});

app.post("/api/posts/:id/save", auth, async (req, res) => {
  const e = await q(`SELECT 1 FROM saved_posts WHERE user_id=$1 AND post_id=$2`, [req.user.id, req.params.id]);
  if (e.length) {
    await q(`DELETE FROM saved_posts WHERE user_id=$1 AND post_id=$2`, [req.user.id, req.params.id]);
  } else {
    await q(`INSERT INTO saved_posts (user_id,post_id) VALUES($1,$2)`, [req.user.id, req.params.id]);
  }
  res.json({ success: true });
});

app.get("/api/saved", auth, async (req, res) => {
  res.json(await q(`SELECT p.* FROM posts p JOIN saved_posts s ON s.post_id=p.id WHERE s.user_id=$1 ORDER BY p.id DESC`, [req.user.id]));
});

app.post("/api/follow/:id", auth, async (req, res) => {
  const target = Number(req.params.id);
  if (target === req.user.id) return res.status(400).json({ success: false });

  const e = await q(`SELECT 1 FROM follows WHERE follower_id=$1 AND following_id=$2`, [req.user.id, target]);
  if (e.length) {
    await q(`DELETE FROM follows WHERE follower_id=$1 AND following_id=$2`, [req.user.id, target]);
  } else {
    await q(`INSERT INTO follows (follower_id,following_id) VALUES($1,$2)`, [req.user.id, target]);
    await notify(target, "follow", "Follower Haaraa", `@${req.user.username} isin hordofaa jira`, { userId: req.user.id });
  }
  res.json({ success: true });
});

app.post("/api/friends/request/:id", auth, async (req, res) => {
  const target = Number(req.params.id);
  const e = await q(`SELECT * FROM friend_requests WHERE sender_id=$1 AND receiver_id=$2 AND status='pending'`, [req.user.id, target]);
  if (!e.length) {
    await q(`INSERT INTO friend_requests (sender_id,receiver_id) VALUES($1,$2)`, [req.user.id, target]);
    await notify(target, "friend_request", "Gaaffii Dhiyoomaa", `@${req.user.username} request siif erge`, { userId: req.user.id });
  }
  res.json({ success: true });
});

app.get("/api/friends/requests", auth, async (req, res) => {
  res.json(await q(`SELECT fr.*,u.username,u.avatar,u.full_name FROM friend_requests fr JOIN users u ON u.id=fr.sender_id WHERE fr.receiver_id=$1 AND fr.status='pending'`, [req.user.id]));
});

app.post("/api/friends/:id/:action", auth, async (req, res) => {
  const id = Number(req.params.id);
  const action = req.params.action;
  if (!["confirm", "reject"].includes(action)) return res.status(400).json({ success: false });

  await q(`UPDATE friend_requests SET status=$1 WHERE id=$2 AND receiver_id=$3`, [action === "confirm" ? "accepted" : "rejected", id, req.user.id]);
  res.json({ success: true });
});

app.get("/api/chat/:userId", auth, async (req, res) => {
  res.json(await q(
    `SELECT m.*, su.username sender_name, ru.username receiver_name FROM messages m JOIN users su ON su.id=m.sender_id JOIN users ru ON ru.id=m.receiver_id WHERE (sender_id=$1 AND receiver_id=$2) OR (sender_id=$2 AND receiver_id=$1) ORDER BY m.id ASC LIMIT 300`,
    [req.user.id, Number(req.params.userId)]
  ));
});

app.post("/api/chat/:userId", auth, async (req, res) => {
  const receiver = Number(req.params.userId);
  const rows = await q(
    `INSERT INTO messages (sender_id,receiver_id,body,media_url,media_type) VALUES($1,$2,$3,$4,$5) RETURNING *`,
    [req.user.id, receiver, req.body.body || "", req.body.media_url || null, req.body.media_type || null]
  );

  for (const [sid, u] of activeUsers) {
    if (u.userId === receiver) {
      io.to(sid).emit("new-message", rows[0]);
    }
  }

  await notify(receiver, "message", "Ergaa Haaraa", `@${req.user.username} ergaa siif erge`, { senderId: req.user.id });
  res.json({ success: true, message: rows[0] });
});

app.get("/api/notifications", auth, async (req, res) => {
  res.json(await q(`SELECT * FROM notifications WHERE user_id=$1 ORDER BY id DESC LIMIT 100`, [req.user.id]));
});

// ADMIN MODERATION ENDPOINTS
app.get("/api/admin/pending-posts", auth, adminOnly, async (req, res) => {
  res.json(await q(`SELECT p.*,u.avatar,u.full_name FROM posts p JOIN users u ON u.id=p.user_id WHERE p.approved=FALSE AND p.rejected=FALSE ORDER BY p.id ASC`));
});

app.post("/api/admin/approve-post", auth, adminOnly, async (req, res) => {
  await q(`UPDATE posts SET approved=TRUE WHERE id=$1`, [req.body.postId]);
  const p = await q("SELECT user_id FROM posts WHERE id=$1", [req.body.postId]);
  if (p.length) {
    await notify(p[0].user_id, "post_approved", "Postii Mirkanaa'e", "Postiin keessan public ta'eera!", { postId: req.body.postId });
  }
  res.json({ success: true });
});

app.post("/api/admin/delete-post", auth, adminOnly, async (req, res) => {
  const p = await q("SELECT user_id FROM posts WHERE id=$1", [req.body.postId]);
  await q(`UPDATE posts SET rejected=TRUE WHERE id=$1`, [req.body.postId]);
  if (p.length) {
    await notify(p[0].user_id, "post_rejected", "Postii Kuffifame", "Postiin keessan seera platformii wajjin waan wal-simateef kuffifameera.", { postId: req.body.postId });
  }
  res.json({ success: true });
});

app.post("/api/admin/ban-user", auth, adminOnly, async (req, res) => {
  const days = Math.max(1, Math.min(365, Number(req.body.days) || 3));
  const rows = await q("SELECT id FROM users WHERE username=$1", [req.body.username]);
  if (!rows.length) return res.status(404).json({ success: false, message: "User not found" });

  await q(`UPDATE users SET banned_until=NOW()+($1 || ' days')::interval WHERE id=$2`, [days, rows[0].id]);
  await notify(rows[0].id, "moderation", "Account Suspended", `Accountiin keessan guyyaa ${days}-f adabameera.`, { days });
  res.json({ success: true });
});

app.post("/api/admin/warn-user", auth, adminOnly, async (req, res) => {
  const rows = await q("SELECT id FROM users WHERE username=$1", [req.body.username]);
  if (!rows.length) return res.status(404).json({ success: false, message: "User not found" });

  await notify(rows[0].id, "warning", "Akeekkachiisa", req.body.reason || "Seeraa fi naamusa hawaasaa kabajaa.", {});
  res.json({ success: true });
});

// REALTIME WEBRTC CALLS & ONLINE STATUS
io.on("connection", socket => {
  socket.on("register-user", async ({ token: t } = {}) => {
    const uid = sessions.get(t);
    if (!uid) return;

    const userRows = await q("SELECT username FROM users WHERE id=$1", [uid]);
    activeUsers.set(socket.id, { userId: uid, username: userRows[0]?.username });
    await broadcastUsers();

    const missed = await q(`SELECT * FROM missed_calls WHERE receiver_id=$1 AND seen=FALSE ORDER BY id DESC`, [uid]);
    if (missed.length) {
      socket.emit("missed-calls", missed);
      await q(`UPDATE missed_calls SET seen=TRUE WHERE receiver_id=$1`, [uid]);
    }
  });

  socket.on("call-user", async data => {
    const target = [...activeUsers.entries()].find(([, u]) => u.username === data.userToCall);
    const caller = activeUsers.get(socket.id);
    if (!caller) return;

    if (target) {
      io.to(target[0]).emit("incoming-call", {
        signal: data.signalData,
        from: socket.id,
        callerName: caller.username,
        isVideo: !!data.isVideo
      });
    } else {
      const r = await q("SELECT id FROM users WHERE username=$1", [data.userToCall]);
      if (r.length) {
        await q(`INSERT INTO missed_calls (caller_id,receiver_id,caller,receiver,call_type) VALUES($1,$2,$3,$4,$5)`, [
          caller.userId, r[0].id, caller.username, data.userToCall, data.isVideo ? "video" : "voice"
        ]);
        await notify(r[0].id, "missed_call", "Missed Call", `@${caller.username} siif bilbilaa ture.`, { caller: caller.username, isVideo: !!data.isVideo });
      }
      socket.emit("call-offline", { username: data.userToCall });
    }
  });

  socket.on("accept-call", d => io.to(d.to).emit("call-accepted", d.signal));
  socket.on("reject-call", d => io.to(d.to).emit("call-rejected"));

  socket.on("disconnect", () => {
    activeUsers.delete(socket.id);
    broadcastUsers();
  });
});

async function broadcastUsers() {
  const users = await q(`SELECT id,username,avatar,full_name,is_vip FROM users ORDER BY username`);
  const online = new Set([...activeUsers.values()].map(x => x.userId));
  io.emit("update-user-list", users.map(u => ({ ...u, isOnline: online.has(u.id) })));
}

app.get("*", (req, res) => res.sendFile(path.join(__dirname, "public", "index.html")));

server.listen(PORT, () => console.log(`Imaanaa Social running on port ${PORT}`));
