import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions.categorical import Categorical
import torch_ac
from GTrXL.gtrxl import GTrXL

# Function from https://github.com/ikostrikov/pytorch-a2c-ppo-acktr/blob/master/model.py
def init_params(m):
    classname = m.__class__.__name__
    if classname.find("Linear") != -1:
        m.weight.data.normal_(0, 1)
        m.weight.data *= 1 / torch.sqrt(m.weight.data.pow(2).sum(1, keepdim=True))
        if m.bias is not None:
            m.bias.data.fill_(0)


class ATransCModel(nn.Module, torch_ac.RecurrentACModel):
    def __init__(self, obs_space, action_space, use_memory=False, use_text=False,nhead=8, num_layers=1,encoder_input_dim=192,decoder_output_dim=128 ,hidden_dim=64,dropout=0.1):
        super().__init__()

        # Decide which components are enabled
        self.use_text = use_text
        self.use_memory = use_memory

        # Define image embedding
        self.image_conv = nn.Sequential(
            nn.Conv2d(3, 16, (2, 2)),
            nn.ReLU(),
            nn.MaxPool2d((2, 2)),
            nn.Conv2d(16, 32, (2, 2)),
            nn.ReLU(),
            nn.Conv2d(32, 64, (2, 2)),
            nn.ReLU()
        )
        n = obs_space["image"][0]
        m = obs_space["image"][1]
        self.image_embedding_size = ((n-1)//2-2)*((m-1)//2-2)*64
        self.transformer = nn.TransformerEncoder(nn.TransformerEncoderLayer(d_model=hidden_dim, nhead=nhead), num_layers=num_layers)

        # Define memory
        if self.use_memory:
            self.memory_rnn = nn.LSTMCell(self.image_embedding_size, self.semi_memory_size)

        # Define text embedding
        if self.use_text:
            self.word_embedding_size = 32
            self.word_embedding = nn.Embedding(obs_space["text"], self.word_embedding_size)
            self.text_embedding_size = 128
            self.text_rnn = nn.GRU(self.word_embedding_size, self.text_embedding_size, batch_first=True)

        # Resize image embedding
        self.embedding_size = self.semi_memory_size
        if self.use_text:
            self.embedding_size += self.text_embedding_size
            self.actor_embedding_size = self.semi_memory_size

        self.actor = nn.Sequential(
            nn.Linear(self.embedding_size, 64),
            nn.ReLU(),
            nn.Linear(64, action_space.n)
        )
        # Define critic's model
        self.critic = nn.Sequential(
            nn.Linear(self.embedding_size, 64),
            nn.ReLU(),
            nn.Linear(64, 1)
        )
        self.apply(init_params)

    @property
    def memory_size(self):
        return 2*self.semi_memory_size

    @property
    def semi_memory_size(self):
        return self.image_embedding_size

    def forward(self, obs, memory):
        x = obs.image.transpose(1, 3).transpose(2, 3)
        x = self.image_conv(x)
        x = x.reshape(x.shape[0], -1)

        if self.use_memory:
            hidden = (memory[:, :self.semi_memory_size], memory[:, self.semi_memory_size:])
            hidden = self.memory_rnn(x, hidden)
            embedding = hidden[0]
            memory = torch.cat(hidden, dim=1)
        else:
            embedding = x

        if self.use_text:
            embed_text = self._get_embed_text(obs.text)
            embedding = torch.cat((embedding, embed_text), dim=1)
        embeddingT = embedding.unsqueeze(0)  # Add sequence dimension
        embeddingT = self.transformer(embeddingT)
        embeddingT = embeddingT.squeeze(0)  # Remove sequence dimension
        x = self.actor(x)
        dist = Categorical(logits=F.log_softmax(x, dim=1))

        x = self.critic(embeddingT)
        value = x.squeeze(1)

        return dist, value, memory

    def _get_embed_text(self, text):
        _, hidden = self.text_rnn(self.word_embedding(text))
        return hidden[-1]

