require('dotenv').config();
const express = require('express');
const http = require('http');
const { Server } = require('socket.io');
const { Pool } = require('pg');
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const cors = require('cors');

const app = express();
const server = http.createServer(app);
const io = new Server(server, { cors: { origin: "*" } });

app.use(cors());
app.use(express.json());
app.use(express.static('public'));

// Neon PostgreSQL Connection
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});

const JWT_SECRET = process.env.JWT_SECRET || 'super_secret_key';
let onlineUsers = new Map(); // userId -> socketId

// Middleware Authenticate
const authenticateToken = (req, res, next) => {
  const token = req.headers['authorization']?.split(' ')[1];
  if (!token) return res.sendStatus(401);
  jwt.verify(token, JWT_SECRET, (err, user) => {
    if (err) return res.sendStatus(403);
    req.user = user;
    next();
  });
};

// 1. SIGNUP
app.post('/api/signup', async (req, res) => {
  const { full_name, username, email, password } = req.body;
  try {
    const hashedPassword = await bcrypt.hash(password, 10);
    const result = await pool.query(
      'INSERT INTO users (full_name, username, email, password) VALUES ($1, $2, $3, $4) RETURNING id, username',
      [full_name, username, email, hashedPassword]
    );
    res.json({ message: "Gallee jirta! Amma Login godhadhu.", user: result.rows[0] });
  } catch (err) {
    res.status(400).json({ error: "Username ama Email maqaamaan jira!" });
  }
});

// 2. LOGIN
app.post('/api/login', async (req, res) => {
  const { username, password } = req.body;
  const result = await pool.query('SELECT * FROM users WHERE username = $1', [username]);
  if (result.rows.length === 0) return res.status(400).json({ error: "User-iin hin argamne!" });

  const user = result.rows[0];
  if (user.is_suspended && new Date(user.suspended_until) > new Date()) {
    return res.status(403).json({ error: `Accountiin kee adabameera hanga: ${user.suspended_until}` });
  }

  const validPassword = await bcrypt.compare(password, user.password);
  if (!validPassword) return res.status(400).json({ error: "Password dogoggora!" });

  const token = jwt.sign({ id: user.id, username: user.username, role: user.role }, JWT_SECRET);
  res.json({ token, user: { id: user.id, username: user.username, full_name: user.full_name, role: user.role, profile_pic: user.profile_pic, is_vip: user.is_vip } });
});

// 3. EDIT PROFILE
app.put('/api/profile', authenticateToken, async (req, res) => {
  const { full_name, bio, profile_pic } = req.body;
  await pool.query(
    'UPDATE users SET full_name = $1, bio = $2, profile_pic = $3 WHERE id = $4',
    [full_name, bio, profile_pic, req.user.id]
  );
  res.json({ message: "Profile update ta'eera!" });
});

// 4. POST CREATION (APPROVAL REQUIRED HIN BEEKAMNE)
app.post('/api/posts', authenticateToken, async (req, res) => {
  const { content, media_url, media_type } = req.body;
  await pool.query(
    'INSERT INTO posts (user_id, content, media_url, media_type, status) VALUES ($1, $2, $3, $4, $5)',
    [req.user.id, content, media_url, media_type, 'pending']
  );
  // Maammilli akkasumas post ta'eera qofa arga!
  res.json({ message: "Postiin keessan milkaa'inaan ergameera!" });
});

// GET APPROVED POSTS
app.get('/api/posts', async (req, res) => {
  const result = await pool.query(`
    SELECT posts.*, users.username, users.profile_pic 
    FROM posts JOIN users ON posts.user_id = users.id 
    WHERE posts.status = 'approved' ORDER BY posts.created_at DESC
  `);
  res.json(result.rows);
});

// 5. ADMIN CONTROL & MODERATION
app.get('/api/admin/pending-posts', authenticateToken, async (req, res) => {
  if (req.user.role !== 'admin') return res.sendStatus(403);
  const result = await pool.query('SELECT posts.*, users.username FROM posts JOIN users ON posts.user_id = users.id WHERE status = $1', ['pending']);
  res.json(result.rows);
});

app.post('/api/admin/approve-post', authenticateToken, async (req, res) => {
  if (req.user.role !== 'admin') return res.sendStatus(403);
  const { post_id, action } = req.body; // 'approved' or 'rejected'
  await pool.query('UPDATE posts SET status = $1 WHERE id = $2', [action, post_id]);
  res.json({ message: `Post status changed to ${action}` });
});

app.post('/api/admin/warn-suspend', authenticateToken, async (req, res) => {
  if (req.user.role !== 'admin') return res.sendStatus(403);
  const { user_id, days, reason } = req.body;
  
  let suspendUntil = new Date();
  suspendUntil.setDate(suspendUntil.getDate() + days);

  await pool.query('UPDATE users SET is_suspended = TRUE, suspended_until = $1 WHERE id = $2', [suspendUntil, user_id]);
  await pool.query('INSERT INTO notifications (user_id, type, message) VALUES ($1, $2, $3)', 
    [user_id, 'warning', `Akeekachiisa: Seera cabsuu keetiif guyyoota ${days} adabamteetta. Sababa: ${reason}`]);
  
  res.json({ message: "Maammilli adabameera!" });
});

// GET ALL USERS (ONLINE & OFFLINE)
app.get('/api/users', authenticateToken, async (req, res) => {
  const result = await pool.query('SELECT id, username, full_name, profile_pic FROM users');
  const usersWithStatus = result.rows.map(u => ({
    ...u,
    is_online: onlineUsers.has(u.id)
  }));
  res.json(usersWithStatus);
});

// MISSED CALLS & NOTIFICATIONS
app.get('/api/notifications', authenticateToken, async (req, res) => {
  const result = await pool.query('SELECT * FROM notifications WHERE user_id = $1 ORDER BY created_at DESC', [req.user.id]);
  res.json(result.rows);
});

// SOCKET.IO REALTIME ENGINE
io.on('connection', (socket) => {
  socket.on('register_user', (userId) => {
    socket.userId = userId;
    onlineUsers.set(userId, socket.id);
    io.emit('user_status_change', { userId, status: 'online' });
  });

  // Call Notification & Ringing
  socket.on('call_user', async ({ toUserId, offer, callerName }) => {
    const receiverSocket = onlineUsers.get(toUserId);
    if (receiverSocket) {
      io.to(receiverSocket).emit('incoming_call', { from: socket.userId, offer, callerName });
    } else {
      // User is Offline -> Save Missed Call Notification
      await pool.query(
        'INSERT INTO notifications (user_id, type, message) VALUES ($1, $2, $3)',
        [toUserId, 'missed_call', `${callerName} irraa yaaliin bilbila video jira (Missed Call)`]
      );
      socket.emit('user_offline', { message: "Maammilli offline daa. Yaaliin bilbilaa (Missed Call) isaaniif ergameera." });
    }
  });

  socket.on('answer_call', ({ toUserId, answer }) => {
    const callerSocket = onlineUsers.get(toUserId);
    if (callerSocket) io.to(callerSocket).emit('call_answered', { answer });
  });

  socket.on('ice_candidate', ({ toUserId, candidate }) => {
    const targetSocket = onlineUsers.get(toUserId);
    if (targetSocket) io.to(targetSocket).emit('ice_candidate', { candidate });
  });

  socket.on('disconnect', () => {
    if (socket.userId) {
      onlineUsers.delete(socket.userId);
      io.emit('user_status_change', { userId: socket.userId, status: 'offline' });
    }
  });
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => console.log(`Server running on port ${PORT}`));
