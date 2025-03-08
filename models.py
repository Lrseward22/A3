from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(100), unique=False, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    address = db.Column(db.String(256), unique=False, nullable=False)
    password = db.Column(db.String(64), unique=False, nullable=True)
    payment = db.Column(db.String(16), unique=True, nullable=False)

    def __repr__(self):
        return f'User: {self.name}, {self.email}'

def create_user(username, name, email, address, payment, password):
    try:
        new_user = User(username=username, name=name, email=email, address=address, payment=payment, password=password)
        db.session.add(new_user)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        return False

def has_user(username):
    return db.session.query(User).filter_by(username=username).first() is not None

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(32), unique=True, nullable=False)
    image = db.Column(db.String(64), unique=True, nullable=False)
    description = db.Column(db.String(1000), unique=False, nullable=False)
    price = db.Column(db.Numeric(10,2),unique = False,nullable=False) 

    def __repr__(self):
        return f'Item: {self.name}'


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    item_id = db.Column(db.Integer, db.ForeignKey('item.id'))
    quantity = db.Column(db.Integer, nullable=False, default=1)

    item = db.relationship('Item', backref='order_item')


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    address = db.Column(db.String, nullable=False)
    total = db.Column(db.Float, nullable=False)

    items = db.relationship('OrderItem', backref='order', cascade='all, delete-orphan')


def add_items():
    items = []
    items.append(add_pocket_watch())
    items.append(add_ice_cream_machine())
    items.append(add_cloak())
    items.append(add_helmet())
    items.append(add_wormhole_generator())
    return items

def add_pocket_watch():
    name = 'Normal Pocket Watch'
    if Item.query.filter_by(name=name).first():
        return name
    image = 'watch.jpg'
    desc = ('This is a totally normal pocket watch that only lets you travel time... '
           'I mean tell time. Nothing other than telling time... except maybe for the occasional '
           'glitch that sends you to different eras. But honestly, who needs reliable time-telling '
           'anyway when you can accidentally end up in the Victorian era or the Jurassic period? '
           'It\'s all fun and games until you realize your meeting starts in five minutes and you\'re '
           'wearing a top hat and monocle.')
    itemPrice = 100.99
    item = Item(name=name, image=image, description=desc,price = itemPrice)
    db.session.add(item)
    db.session.commit()
    return name

def add_ice_cream_machine():
    name = 'Normal Ice Cream Machine'
    if Item.query.filter_by(name=name).first():
        return name
    image = 'ice_cream.jpg'
    desc = ('This ice cream machine allows you to have ice cream of any flavor you desire. It has all toppings you can think of. '
           'You, also, never have to do any maintanence. Nothing special really... except maybe the fact that it conjures the creamiest, most heavenly scoops '
           'with just a thought. Imagine a machine that reads your deepest dessert dreams and manifests them instantly. And no maintenance? That\'s right, it\'s '
           'self-cleaning and as if by magic, it never runs out of ingredients. It\'s like having your personal Willy Wonka in your kitchen.')
    itemPrice = 52.49
    item = Item(name=name, image=image, description=desc,price = itemPrice)
    db.session.add(item)
    db.session.commit()
    return name

def add_cloak():
    name = 'Normal Cloak'
    if Item.query.filter_by(name=name).first():
        return name
    image = 'cloak.jpg'
    desc = ('Like all cloaks, it is super cool and makes you look cool. Or I suppose it makes you look not at all since people cannot see you. '
           'Did I mention that? It makes you invisible. Perfect for slipping out of boring meetings or sneaking into secret wizard gatherings. '
           'But be careful not to get lost in your own invisibility-after all, with great power comes great responsibility. We wouldn\'t want '
           'any illegal things happening. Whether you\'re avoiding an awkward encounter or seeking a thrilling adventure, this cloak has got you '
           'covered... literally.')
    itemPrice = 10.00
    item = Item(name=name, image=image, description=desc,price = itemPrice)
    db.session.add(item)
    db.session.commit()
    return name

def add_helmet():
    name = 'Normal Helmet'
    if Item.query.filter_by(name=name).first():
        return name
    image = 'helmet.jpg'
    desc = ('This football helmet provides superb protection. Ironically, it also increases the neural efficiency of the wearer. That\'s funny '
           'because football gives you concussions which can lower brain function. Imagine a helmet that not only guards your noggin but also '
           'sharpens it! While it keeps you safe from those brutal tackles, or whatever you need a helmet for these days, it somehow also boosts '
           'your brainpower-turning you into a gridiron genius. But beware, even a super-smart helmet can\'t stop you from forgetting where you '
           'your keys after the game. Ideal for the player who wants to outthink the competition, both on and off the field. It\'s all about '
           'about balancing brawn with brains.')
    itemPrice = 12.00
    item = Item(name=name, image=image, description=desc,price = itemPrice)
    db.session.add(item)
    db.session.commit()
    return name

def add_wormhole_generator():
    name = 'Normal Wormhole Generator'
    if Item.query.filter_by(name=name).first():
        return name
    image = 'generator.jpg'
    desc = ('This generator is a proprietary technology that creates wormholes. These incredibly intricate machines allow you to travel long '
           'distances in just a few steps. User manual sold seperately, but who needs instruction for interdimensional travel anyway? Just '
           'press the shiny red button and hope for the best. Nothing bad as ever happened from shiny red buttons, right? Whether you\'re '
           'late for a meeting on the other side of the planet or simply curious about what\'s in your neighbor\'s fridge. This handy gadget '
           'has got you covered. Just remember to close the wormhole behind you. You don\'t want any unexpected visitors from alternate '
           'realities crashing your party. Unless you do for some reason. We don\'t judge, just take your money.')
    itemPrice = 100000.00
    item = Item(name=name, image=image, description=desc,price = itemPrice)
    db.session.add(item)
    db.session.commit()
    return name

if __name__ == '__main__':
    print("hello world")
