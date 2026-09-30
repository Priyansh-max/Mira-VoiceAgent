// Mirrors the fictional local fixtures in backend/tools.py.
export const DEMO_CALLERS = [
  { customerId: 'cust_1001', callerName: 'John Doe', phone: '+1 415 555 3321', order: { id: '1235', status: 'Preparing for shipment' }, ticket: { id: '4822', status: 'Under review', priority: 'Normal' } },
  { customerId: 'cust_1002', callerName: 'John Carper', phone: '+1 212 555 1198', order: { id: '1234', status: 'Shipped today' }, ticket: { id: '4821', status: 'Refund pending', priority: 'High' } },
  { customerId: 'cust_1003', callerName: 'Priya Sharma', phone: '+91 98765 47784', order: { id: '2203', status: 'Delivered yesterday' }, ticket: { id: '5903', status: 'Resolved', priority: 'Normal' } },
  { customerId: 'cust_1004', callerName: 'Arjun Mehta', phone: '+91 98100 24680', order: { id: '2204', status: 'Out for delivery' }, ticket: { id: '5904', status: 'Awaiting response', priority: 'High' } },
  { customerId: 'cust_1005', callerName: 'Emily Carter', phone: '+1 202 555 0147', order: { id: '2205', status: 'Processing' }, ticket: { id: '5905', status: 'Open', priority: 'Normal' } },
  { customerId: 'cust_1006', callerName: 'Miguel Santos', phone: '+34 612 345 786', order: { id: '2206', status: 'Held at customs' }, ticket: { id: '5906', status: 'Escalated', priority: 'High' } },
  { customerId: 'cust_1007', callerName: 'Aisha Khan', phone: '+971 50 555 2714', order: { id: '2207', status: 'Shipped' }, ticket: { id: '5907', status: 'In progress', priority: 'Normal' } },
  { customerId: 'cust_1008', callerName: 'Yuki Tanaka', phone: '+81 90 1234 8462', order: { id: '2208', status: 'Ready for pickup' }, ticket: { id: '5908', status: 'Waiting on carrier', priority: 'Normal' } },
  { customerId: 'cust_1009', callerName: 'Oliver Muller', phone: '+49 151 2345 6039', order: { id: '2209', status: 'Return received' }, ticket: { id: '5909', status: 'Refund approved', priority: 'Normal' } },
  { customerId: 'cust_1010', callerName: 'Thandiwe Ndlovu', phone: '+27 82 555 9146', order: { id: '2210', status: 'Delayed in transit' }, ticket: { id: '5910', status: 'Under review', priority: 'High' } },
];

export const DEMO_TEST_HINTS = [
  'Say “John Carver” with 1198 to test fuzzy name matching.',
  'Decline a requested name, order ID, or ticket ID to test the support callback.',
];
