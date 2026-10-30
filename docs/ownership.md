# Who owns what

The rule: a service writes only the data it owns, and reads other services only
through the topics listed below. No service queries another service's tables.

## Data

| Data                                          | Owner (only writer)  | Visible to others                 |
|-----------------------------------------------|----------------------|-----------------------------------|
| users, refresh tokens, listing metadata       | core api             | no                                |
| reserve price                                 | core api             | bidding only, via listing-events  |
| auction state: price, leader, status, deadline| bidding              | public part via auction-updates   |
| bids, orders, bidding outbox                  | bidding              | no                                |
| WebSocket connections, hub state              | realtime             | none; it has no database          |

## Topics

| Topic              | Producer | Consumer(s)  | Audience                         |
|--------------------|----------|--------------|----------------------------------|
| listing-events     | core api | bidding      | private (contains reserve price) |
| auction-updates    | bidding  | realtime     | public (any viewer)              |
| auction-outcomes   | bidding  | core api     | private (names the winner)       |

## Event types

| Event            | Topic            | Meaning                                    |
|------------------|------------------|--------------------------------------------|
| listing.published| listing-events   | seller published; bidding starts tracking  |
| auction.updated  | auction-updates  | the public state changed (price, status)   |
| auction.settled  | auction-outcomes | the auction closed: sold or unsold         |

## Why the topics are split

Realtime is the one consumer that talks to browsers. If it could read the outcomes
topic, a bug in the WebSocket code could send a winner's identity to every viewer.
Keeping the topics separate means realtime cannot receive private data at all.