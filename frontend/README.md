# Backlog Beater Frontend

A Next.js React application for displaying and managing your game backlog.

## Features

- View all games in your backlog
- Display game status, hours played, and progress
- Responsive design with Tailwind CSS
- JWT authentication integration

## Getting Started

1. Ensure your backend services are running (user-service, backlog-service, etc.)

2. Run the development server:

```bash
npm run dev
```

3. Open [http://localhost:3000](http://localhost:3000) in your browser

## Authentication

The app expects a JWT token stored in `localStorage` under the key `jwt_token`. You'll need to obtain this token from your user-service login endpoint.

## API Integration

The app fetches backlog data from `http://localhost:8002/backlog` with Bearer token authentication.

## Tech Stack

- Next.js 16
- React 18
- TypeScript
- Tailwind CSS
- ESLint

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
